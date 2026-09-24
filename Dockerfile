# One container: the React app built into frontend/dist, served with /api by uvicorn.
# docs/architecture.md § Deployment. State lives in the database named by
# ONEGUARD_DATABASE_URL (Supabase); secrets come from the environment, never the image.

# --- frontend/dist (built only when the frontend is in the build context) -------------
FROM node:22-slim AS frontend
WORKDIR /src
COPY . .
# Relative /api, real backend: one origin, no CORS (frontend/README.md §1).
ENV VITE_API_BASE_URL=/api VITE_USE_MOCKS=false
RUN mkdir -p /dist && if [ -f frontend/package.json ]; then \
      cd frontend && npm ci --no-audit --no-fund && npm run build && cp -r dist/. /dist/; \
    else echo "no frontend/package.json: serving the placeholder page"; fi

# --- the app ---------------------------------------------------------------------------
FROM python:3.12-slim
# tzdata: Europe/Zurich rules for night-time and weekday checks (rules.md W5, C12).
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/opt/huggingface
WORKDIR /app
# Dependencies first, from pyproject.toml alone (an empty package stands in for the code),
# so a code change reuses the torch and checkpoint layers. torch is the CPU-only wheel from
# the PyTorch index; the signals extra (Laya) then finds it installed and keeps it, so no
# CUDA wheel reaches the image. The compiler extra brings the provider SDKs (C1 on the LLM
# path, tier-2 facts, tier-3 rewrites).
COPY backend/pyproject.toml backend/
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch \
    && mkdir backend/oneguard && touch backend/oneguard/__init__.py \
    && pip install -e "backend[compiler,signals]" \
    && python -c "import torch; assert not torch.version.cuda, torch.__version__"
# The Laya checkpoint signals.py asks first (CHECKPOINTS[0]), downloaded and loaded once at
# build time into HF_HOME, so the machine never fetches it: HF_HUB_OFFLINE keeps startup off
# the Hub and signals.warm() loads it from the image.
RUN python -c "from laya import Router; Router(max_loaded=1).load('laya-typed-decisions')" \
    && chmod -R a+rX /opt/huggingface
ENV HF_HUB_OFFLINE=1
COPY backend/oneguard backend/oneguard
# scripts/: bench_engine.py --laya measures the model on the machine (docs/benchmark.md).
COPY backend/scripts backend/scripts
# Editable, so the package finds data/ next to it (store/seed.py DATA_DIR).
RUN pip install --no-deps -e backend
COPY data data
COPY --from=frontend /dist frontend/dist
RUN useradd --system --uid 10001 oneguard && chown -R oneguard /app
USER oneguard
ENV PORT=8080 ONEGUARD_ENV=prod ONEGUARD_SOFT_SIGNALS=laya
WORKDIR /app/backend
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn oneguard.api.app:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
