# Home School monorepo entry points. Packages are listed in packages.json;
# planned packages and missing tools are skipped with a message (HS_STRICT=1 makes missing tools fail).
PY ?= python3

.PHONY: help test lint layout secrets hooks tools

help:
	@echo "make test     run every implemented package's tests (skips planned packages with a message)"
	@echo "make lint     layout check, secret scan, per-package lint"
	@echo "make layout   only the layout check"
	@echo "make secrets  only the secret scan"
	@echo "make hooks    install pre-commit hooks (needs pre-commit)"
	@echo "make tools    show which tools are installed"
	@echo "Env: HS_SKIP=id,id skips package ids; HS_STRICT=1 fails when a tool is missing (CI)."

test:
	@$(PY) scripts/run_packages.py test

lint: layout secrets
	@$(PY) scripts/run_packages.py lint

layout:
	@$(PY) scripts/check_layout.py

secrets:
	@$(PY) scripts/secret_scan.py

hooks:
	@command -v pre-commit >/dev/null 2>&1 && pre-commit install || echo "pre-commit not installed: pipx install pre-commit (or uv tool install pre-commit), then re-run make hooks. 'make lint' covers the same checks."

tools:
	@for t in python3 make git uv node npm flutter dart pre-commit gitleaks; do \
	  if command -v $$t >/dev/null 2>&1; then echo "ok       $$t"; else echo "missing  $$t"; fi; done
