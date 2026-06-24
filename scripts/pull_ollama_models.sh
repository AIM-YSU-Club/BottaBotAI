#!/usr/bin/env bash
set -euo pipefail

OLLAMA_HOST="${OLLAMA_HOST:-http://localhost:11434}"

MODELS=(
  "nomic-embed-text"
  "llama3.2"
)

for model in "${MODELS[@]}"; do
  echo "Pulling ${model}..."
  curl -sf "${OLLAMA_HOST}/api/pull" -d "{\"name\": \"${model}\"}"
  echo
done

echo "Done."
