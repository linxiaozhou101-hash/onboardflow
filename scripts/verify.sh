#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
.venv/bin/python -m pytest backend/tests -q
.venv/bin/python scripts/evaluate.py
npm --prefix frontend run build
npm --prefix frontend run test:e2e
