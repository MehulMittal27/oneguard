SCEN ?= SCEN0000
PYTHON ?= $(shell command -v python3.12 2>/dev/null || uv python find 3.12 2>/dev/null || echo python3.12)
FRONTEND_SKIP = echo "skipping frontend step: frontend/package.json not found (UI not copied in yet)"

setup:
	cd backend && $(PYTHON) -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
	@if [ -f frontend/package.json ]; then cd frontend && npm install; else $(FRONTEND_SKIP); fi

test:
	cd backend && . .venv/bin/activate && pytest -q

lint:
	cd backend && . .venv/bin/activate && ruff check .
	@if [ -f frontend/package.json ]; then cd frontend && npm run lint; else $(FRONTEND_SKIP); fi

replay:
	cd backend && . .venv/bin/activate && python -m oneguard.replay.runner --scenario $(SCEN)

replay-all:
	cd backend && . .venv/bin/activate && python -m oneguard.replay.runner --all

dev:
	cd backend && . .venv/bin/activate && uvicorn oneguard.api.app:app --reload --port 8000 &
	@if [ -f frontend/package.json ]; then cd frontend && npm run dev; else $(FRONTEND_SKIP); fi

build-frontend:
	@if [ -f frontend/package.json ]; then cd frontend && npm run build; else $(FRONTEND_SKIP); fi

serve: build-frontend
	cd backend && . .venv/bin/activate && uvicorn oneguard.api.app:app --port 8000

demo-offline:
	curl -s -X POST localhost:8000/api/dev/replay/restart -H 'Content-Type: application/json' \
	  -d '{"scenario_id":"$(SCEN)","card_id":"$(CARD)","speed_ms":4000}'

demo-live:
	cd backend && . .venv/bin/activate && python -m oneguard.viseca.demo --scenario $(SCEN)

.PHONY: setup test lint replay replay-all dev build-frontend serve demo-offline demo-live
