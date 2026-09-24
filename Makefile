SCEN ?= SCEN0000
PYTHON ?= $(shell command -v python3.12 2>/dev/null || uv python find 3.12 2>/dev/null || echo python3.12)

setup:
	cd backend && $(PYTHON) -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
	cd frontend && npm install

test:
	cd backend && . .venv/bin/activate && pytest -q

lint:
	cd backend && . .venv/bin/activate && ruff check .
	cd frontend && npm run lint

check:
	cd backend && . .venv/bin/activate && ruff check . && pytest -q

seed:
	cd backend && . .venv/bin/activate && python -m oneguard.store.seed

reset-db:
	cd backend && . .venv/bin/activate && python -m oneguard.store.seed --reset

replay:
	cd backend && . .venv/bin/activate && python -m oneguard.replay.runner --scenario $(SCEN) --policy tests/fixtures/policies/$(SCEN).yaml

replay-all:
	cd backend && . .venv/bin/activate && python -m oneguard.replay.runner --all

dev:
	cd backend && . .venv/bin/activate && uvicorn oneguard.api.app:app --reload --port 8000 &
	cd frontend && npm run dev

build-frontend:
	cd frontend && npm run build

serve: build-frontend
	cd backend && . .venv/bin/activate && uvicorn oneguard.api.app:app --port 8000

demo-offline:
	curl -s -X POST localhost:8000/api/dev/replay/restart -H 'Content-Type: application/json' \
	  -d '{"scenario_id":"$(SCEN)","card_id":"$(CARD)","speed_ms":4000}'

demo-live:
	cd backend && . .venv/bin/activate && python -m oneguard.viseca.demo --scenario $(SCEN)

# The image Fly builds (Dockerfile); runs locally with `docker run -p 8080:8080 --env-file .env oneguard`.
image:
	docker build -t oneguard .

# One machine in lhr (fly.toml); secrets are set on the app with `fly secrets`, never here.
deploy:
	fly deploy -a oneguard --ha=false

logs:
	fly logs -a oneguard

.PHONY: setup test lint check seed reset-db replay replay-all dev build-frontend serve demo-offline demo-live image deploy logs
