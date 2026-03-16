.PHONY: backend frontend test protocol-fixtures acceptance replay-frontend demo-backup demo-option-a backend-venv reliability

BACKEND_PYTHON := $(shell if [ -x backend/.venv/bin/python ]; then echo backend/.venv/bin/python; else echo python3; fi)

backend:
	cd backend && $(BACKEND_PYTHON) -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

frontend:
	cd frontend && npm run dev

test:
	cd backend && $(BACKEND_PYTHON) -m pytest
	cd frontend && npm run test

reliability:
	cd backend && $(BACKEND_PYTHON) -m pytest
	cd frontend && npm run test
	cd .opencode && node --test tests/lumonPluginCore.test.js
	node scripts/run_opencode_reliability_harness.mjs --scenario external,local,approval,second-task
	$(BACKEND_PYTHON) scripts/run_transport_reliability_checks.py

protocol-fixtures:
	cd backend && $(BACKEND_PYTHON) -m app.fixtures.replay

backend-venv:
	./scripts/bootstrap_backend_venv.sh

acceptance:
	./scripts/run_acceptance.sh

replay-frontend:
	./scripts/start_demo_frontend_replay.sh

demo-backup:
	./scripts/start_demo_backend_backup.sh

demo-option-a:
	./scripts/start_demo_backend_option_a.sh
