#!/bin/sh
# =============================================================================
# Ollama 컨테이너 엔트리포인트 (POSIX sh)
#
# 왜 빌드 타임 pull 대신 기동 시 pull 인가?
# - docker-compose 의 ./ollama_data:/root/.ollama 볼륨이 이미지 내부
#   /root/.ollama 를 덮어쓴다.
# - 따라서 Dockerfile RUN 단계에서 pull 한 모델은 볼륨이 비어 있으면
#   컨테이너 시작 후 보이지 않는다.
# - 공유 볼륨에 한 번 pull 되면 이후 재시작에서는 빠르게 스킵/갱신된다.
#
# 모델 목록 출처 (.env → compose env_file):
# - OLLAMA_EMBEDDING_MODEL
# - OLLAMA_CHAT_LLM
# - OLLAMA_SUMMARY_LLM
# 선택: OLLAMA_PULL_MODELS 가 있으면 위 변수 대신 이 목록만 사용
# =============================================================================
set -eu

append_unique() {
  # $1 = accumulator (newline-separated), $2 = candidate model name
  acc="$1"
  candidate="$2"
  [ -z "$candidate" ] && { printf '%s' "$acc"; return; }
  printf '%s\n' "$acc" | grep -Fxq "$candidate" 2>/dev/null && { printf '%s' "$acc"; return; }
  if [ -z "$acc" ]; then
    printf '%s' "$candidate"
  else
    printf '%s\n%s' "$acc" "$candidate"
  fi
}

MODELS=""
if [ -n "${OLLAMA_PULL_MODELS:-}" ]; then
  # shell word-split override list
  # shellcheck disable=SC2086
  set -- $OLLAMA_PULL_MODELS
  for model in "$@"; do
    MODELS="$(append_unique "$MODELS" "$model")"
  done
else
  MODELS="$(append_unique "$MODELS" "${OLLAMA_EMBEDDING_MODEL:-}")"
  MODELS="$(append_unique "$MODELS" "${OLLAMA_CHAT_LLM:-}")"
  MODELS="$(append_unique "$MODELS" "${OLLAMA_SUMMARY_LLM:-}")"
fi

if [ -z "$MODELS" ]; then
  echo "[ollama-entrypoint] ERROR: no models configured." >&2
  echo "  Set OLLAMA_EMBEDDING_MODEL / OLLAMA_CHAT_LLM / OLLAMA_SUMMARY_LLM in .env" >&2
  echo "  or OLLAMA_PULL_MODELS as a space-separated override." >&2
  exit 1
fi

echo "[ollama-entrypoint] models from env:"
printf '%s\n' "$MODELS" | while IFS= read -r model; do
  [ -n "$model" ] && echo "  - $model"
done

echo "[ollama-entrypoint] starting ollama serve..."
ollama serve &
OLLAMA_PID=$!

cleanup() {
  echo "[ollama-entrypoint] shutting down (pid=${OLLAMA_PID})..."
  kill "${OLLAMA_PID}" 2>/dev/null || true
  wait "${OLLAMA_PID}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "[ollama-entrypoint] waiting for API to become ready..."
ready=0
i=0
while [ "$i" -lt 90 ]; do
  if ollama list >/dev/null 2>&1; then
    ready=1
    break
  fi
  i=$((i + 1))
  sleep 1
done

if [ "$ready" -ne 1 ]; then
  echo "[ollama-entrypoint] ERROR: ollama API did not become ready in time" >&2
  exit 1
fi

echo "[ollama-entrypoint] ensuring models exist on shared volume (/root/.ollama)..."
OLD_IFS=$IFS
IFS='
'
# shellcheck disable=SC2086
set -- $MODELS
IFS=$OLD_IFS
for model in "$@"; do
  [ -z "$model" ] && continue
  echo "[ollama-entrypoint] pull: ${model}"
  ollama pull "${model}"
done

echo "[ollama-entrypoint] models ready:"
ollama list || true

echo "[ollama-entrypoint] handing off to ollama serve (pid=${OLLAMA_PID})"
trap - EXIT INT TERM
wait "${OLLAMA_PID}"
