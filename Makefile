# silent-drift-lab — one-command targets.
#
#   make setup        create .venv and install pinned deps
#   make opik-up      start self-hosted Opik (pinned version) → http://localhost:5173
#   make opik-down    stop Opik
#   make services     start the A2A quiz service in the background
#   make run          one interactive study session, traced
#   make demo         the 2-minute scripted demo (healthy run → drift → scores fall)
#   make seed         run scripted sessions and build the Opik dataset from traces
#   make experiment   offline evaluation: evaluate() over the dataset with a local judge
#   make rules        create online evaluation rules + the alert webhook
#   make drift-demo   inject drift, run the monitor, produce reports/drift-figure.png
#   make test         unit tests (fast) ; make evals-test runs the eval gate
#   make lint         ruff over the lab's own code (config in pyproject.toml)
#   make otel-up      start the OpenTelemetry Collector (TELEMETRY_MODE=otel)

.PHONY: footprint setup opik-up opik-down opik-logs services stop run demo seed experiment rules drift-demo test evals-test lint otel-up otel-down lock clean

VENV   = .venv
PY     = $(VENV)/bin/python
OPIK_VERSION ?= 2.2.96
OPIK_SRC ?= .opik

setup:
	uv venv --python 3.11 $(VENV)
	uv pip install -r requirements.txt
	@cp -n .env.example .env 2>/dev/null || true
	@mkdir -p data reports
	@echo "Next: ollama pull qwen2.5:7b && make opik-up && make services && make run"

lock:
	uv pip freeze > requirements.lock

footprint:
	$(PY) scripts/footprint.py

# ── Opik (self-hosted) ──────────────────────────────────────────────────────
$(OPIK_SRC):
	git clone --depth 1 --branch $(OPIK_VERSION) https://github.com/comet-ml/opik.git $(OPIK_SRC)

opik-up: $(OPIK_SRC)
	cd $(OPIK_SRC) && OPIK_VERSION=$(OPIK_VERSION) ./opik.sh
	@echo "Opik UI → http://localhost:5173   (pinned $(OPIK_VERSION))"

opik-down:
	cd $(OPIK_SRC) && ./opik.sh --stop

opik-logs:
	cd $(OPIK_SRC)/deployment/docker-compose && docker compose logs -f backend

# ── App ─────────────────────────────────────────────────────────────────────
services: stop
	@mkdir -p data
	@PYTHONUNBUFFERED=1 nohup $(PY) app/a2a_services/quiz_service.py > data/quiz_service.log 2>&1 &
	@sleep 4
	@echo "Quiz Generator A2A: http://localhost:9001/.well-known/agent-card.json"

stop:
	@pkill -f "a2a_services/quiz_service.py" 2>/dev/null || true

run:
	$(PY) main.py

demo:
	@$(PY) scripts/demo.py

seed:
	$(PY) scripts/seed_dataset.py

experiment:
	$(PY) evals/run_experiment.py

rules:
	$(PY) scripts/create_rules.py

drift-demo:
	$(PY) drift/run_lab.py

test:
	$(PY) -m pytest tests/ -m "not eval" -q

evals-test:
	$(PY) -m pytest tests/ -m eval -q

lint:
	$(VENV)/bin/ruff check .

# ── OpenTelemetry track ─────────────────────────────────────────────────────
otel-up:
	docker compose -f collector/docker-compose.collector.yml up -d
	@echo "Collector: OTLP gRPC :4317, HTTP :4318 → Opik"

otel-down:
	docker compose -f collector/docker-compose.collector.yml down

clean: stop
	rm -rf data/*.db data/*.db-* data/*.log reports/*.png __pycache__ .pytest_cache
