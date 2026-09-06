#!/usr/bin/env bash
# 本地起服务。
#   ./scripts/dev.sh          后端 + 已构建前端，单进程，访问 http://localhost:8000
#   ./scripts/dev.sh --watch  后端 + Vite dev server（热更新），访问 http://localhost:5173
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=.

brew services list | grep -qE '^postgresql@16\s+started' || brew services start postgresql@16
brew services list | grep -qE '^redis\s+started' || brew services start redis

if [[ "${1:-}" == "--watch" ]]; then
  uv run uvicorn app.api.main:app --port 8000 --reload &
  trap 'kill 0' EXIT INT TERM
  cd frontend && pnpm dev
else
  [[ -d frontend/dist ]] || (cd frontend && pnpm build)
  echo "→ http://localhost:8000"
  uv run uvicorn app.api.main:app --port 8000
fi
