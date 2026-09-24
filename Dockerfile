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
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY backend/pyproject.toml backend/
COPY backend/oneguard backend/oneguard
# Editable, so the package finds data/ next to it (store/seed.py DATA_DIR). The compiler
# extra brings the provider SDKs (C1 on the LLM path, tier-2 facts, tier-3 rewrites); the
# signals extra (Laya) stays out of the image: the cloud runs keyword soft signals.
RUN pip install -e "backend[compiler]"
COPY data data
COPY --from=frontend /dist frontend/dist
RUN useradd --system --uid 10001 oneguard && chown -R oneguard /app
USER oneguard
ENV PORT=8080 ONEGUARD_ENV=prod ONEGUARD_SOFT_SIGNALS=keywords
WORKDIR /app/backend
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn oneguard.api.app:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
