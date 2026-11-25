# Home School monorepo entry points. Packages are listed in packages.json;
# planned packages and missing tools are skipped with a message (HS_STRICT=1 makes missing tools fail).
PY ?= python3

.PHONY: help setup test lint layout secrets pylint hooks tools

help:
	@echo "make setup    install package dependencies a clean checkout needs (npm ci in apps/admin)"
	@echo "make test     run every implemented package's tests (skips planned packages with a message)"
	@echo "make lint     layout check, secret scan, per-package lint"
	@echo "make layout   only the layout check"
	@echo "make secrets  only the secret scan"
	@echo "make pylint    ruff check + format --check on scripts (skips if ruff/uvx missing)"
	@echo "make hooks    install pre-commit hooks (needs pre-commit)"
	@echo "make tools    show which tools are installed"
	@echo "Env: HS_SKIP=id,id skips package ids; HS_STRICT=1 fails when a tool is missing (CI)."

setup:
	@if command -v npm >/dev/null 2>&1; then (cd apps/admin && npm ci); else echo "SKIP setup apps/admin: npm not installed"; fi

test:
	@$(PY) scripts/run_packages.py test

lint: layout secrets pylint
	@$(PY) scripts/run_packages.py lint

layout:
	@$(PY) scripts/check_layout.py

secrets:
	@$(PY) scripts/secret_scan.py

pylint:
	@if command -v ruff >/dev/null 2>&1; then R="ruff"; elif command -v uvx >/dev/null 2>&1; then R="uvx ruff"; \
	else if [ "$$HS_STRICT" = "1" ]; then echo "FAIL ruff/uvx missing"; exit 1; fi; echo "SKIP ruff scripts: ruff/uvx not installed"; exit 0; fi; \
	$$R check scripts && $$R format --check scripts

hooks:
	@command -v pre-commit >/dev/null 2>&1 && pre-commit install || echo "pre-commit not installed: pipx install pre-commit (or uv tool install pre-commit), then re-run make hooks. 'make lint' covers the same checks."

tools:
	@for t in python3 make git uv node npm flutter dart pre-commit gitleaks; do \
	  if command -v $$t >/dev/null 2>&1; then echo "ok       $$t"; else echo "missing  $$t"; fi; done
