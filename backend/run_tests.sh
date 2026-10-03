#!/usr/bin/env bash
# Run the backend test suite (skipping the long-running SSE smoke test).
set -euo pipefail
cd "$(dirname "$0")"
export DATABASE_URL="postgresql+asyncpg://workshop_user:workshop_pass@localhost:5432/workshop_reservations"
export CORS_ORIGINS='["http://localhost"]'
export AUTH_SECRET_KEY="test-secret-key-at-least-32-characters-long-for-tests"
export ADMIN_EMAIL="${ADMIN_EMAIL:-admin@example.com}"
# The test stack runs on plain HTTP, so the session
# cookie must not carry the Secure attribute.
export COOKIE_SECURE="false"
exec .venv/bin/python -m pytest --tb=short -q --timeout=15 \
  --deselect libs/reservation/tests/test_workshops_router.py::test_sse_streams_initial_comment \
  "$@"
