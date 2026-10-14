#!/bin/bash
# Sources .env (if present) and runs the Solara app.
# Usage: ./run_solara.sh [filename] [port]
SOLARA_FILE="${1:-solara_app.py}"
PORT="${2:-8900}"
if [ -f .env ]; then
  while IFS= read -r line; do
    [[ $line =~ ^#.*$ || -z $line ]] && continue
    if [[ $line =~ ^([^=]+)=(.*)$ ]]; then
      name="${BASH_REMATCH[1]}"; value="${BASH_REMATCH[2]}"
      value="${value#[\"\']}"; value="${value%[\"\']}"
      export "$name=$value"
    fi
  done < .env
fi
solara run "$SOLARA_FILE" --port "$PORT" --no-open
