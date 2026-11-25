#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
.venv/bin/python -m pytest backend/tests -q --junitxml=reports/qa_20261124/backend-final.xml
.venv/bin/python scripts/evaluate.py --output-dir reports/qa_20261124
npm --prefix frontend run build
npm --prefix frontend run test:e2e -- --config playwright.qa.config.ts
.venv/bin/python scripts/qa_load.py
# Optional network-dependent public package metadata check:
# .venv/bin/python scripts/qa_dependencies.py
