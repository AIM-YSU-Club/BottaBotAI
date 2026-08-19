#!/bin/sh
# =============================================================================
# Ollama 컨테이너 엔트리포인트 (POSIX sh)
#
# 왜 빌드 타임 pull 대신 기동 시 pull 인가?
# - docker-compose 의 ./ollama_data:/root/.ollama 볼륨이 이미지 내부
#   /root/.ollama 를 덮어쓴다.
# - 따라서 Dockerfile RUN 단계에서 pull 한 모델은 볼륨이 비어 있으면
#   컨테이너 시작 후 보이지 않는다.
# - 공유 볼륨에 이미 있는 모델은 pull 하지 않는다.
#
# 모델 목록 출처 (.env → compose env_file):
# - OLLAMA_MODELS : 공백 구분 모델 목록 (필수)
# - 목록에 없는 설치 모델은 기동 시 삭제한다.
# =============================================================================
set -eu

READY_STAMP=/tmp/ollama-models-ready
rm -f "${READY_STAMP}"

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

# ollama list 의 NAME 과 pull 인자를 같은 키로 비교하기 위해 태그가 없으면 :latest 를 붙인다.
normalize_model() {
  name="$1"
  [ -z "$name" ] && return
  case "$name" in
    *:*)
      printf '%s' "$name"
      ;;
    *)
      printf '%s:latest' "$name"
      ;;
  esac
}

is_desired_model() {
  candidate="$(normalize_model "$1")"
  [ -z "$candidate" ] && return 1
  printf '%s\n' "$NORMALIZED_MODELS" | grep -Fxq "$candidate" 2>/dev/null
}

is_installed_model() {
  candidate="$(normalize_model "$1")"
  [ -z "$candidate" ] && return 1
  printf '%s\n' "$INSTALLED_NORMALIZED" | grep -Fxq "$candidate" 2>/dev/null
}

MODELS=""
if [ -z "${OLLAMA_MODELS:-}" ]; then
  echo "[ollama-entrypoint] ERROR: OLLAMA_MODELS is empty." >&2
  echo "  Set OLLAMA_MODELS in .env as a space-separated list of Ollama models." >&2
  exit 1
fi

# shell word-split OLLAMA_MODELS
# shellcheck disable=SC2086
set -- $OLLAMA_MODELS
for model in "$@"; do
  MODELS="$(append_unique "$MODELS" "$model")"
done

# HuggingFace 스타일(org/name) 모델은 Ollama pull 대상이 아니므로 제외
FILTERED=""
OLD_IFS=$IFS
IFS='
'
# shellcheck disable=SC2086
set -- $MODELS
IFS=$OLD_IFS
for model in "$@"; do
  case "$model" in
    */*)
      echo "[ollama-entrypoint] skip non-ollama model: $model" >&2
      ;;
    "")
      ;;
    *)
      FILTERED="$(append_unique "$FILTERED" "$model")"
      ;;
  esac
done
MODELS="$FILTERED"

if [ -z "$MODELS" ]; then
  echo "[ollama-entrypoint] ERROR: no Ollama models left after filtering OLLAMA_MODELS." >&2
  exit 1
fi

NORMALIZED_MODELS=""
OLD_IFS=$IFS
IFS='
'
# shellcheck disable=SC2086
set -- $MODELS
IFS=$OLD_IFS
for model in "$@"; do
  NORMALIZED_MODELS="$(append_unique "$NORMALIZED_MODELS" "$(normalize_model "$model")")"
done

echo "[ollama-entrypoint] models from OLLAMA_MODELS:"
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

echo "[ollama-entrypoint] removing models not listed in OLLAMA_MODELS..."
INSTALLED="$(ollama list 2>/dev/null | awk 'NR > 1 { print $1 }')"
OLD_IFS=$IFS
IFS='
'
# shellcheck disable=SC2086
set -- $INSTALLED
IFS=$OLD_IFS
for installed in "$@"; do
  [ -z "$installed" ] && continue
  if is_desired_model "$installed"; then
    continue
  fi
  echo "[ollama-entrypoint] rm: ${installed}"
  ollama rm "${installed}"
done

INSTALLED_NORMALIZED=""
INSTALLED="$(ollama list 2>/dev/null | awk 'NR > 1 { print $1 }')"
OLD_IFS=$IFS
IFS='
'
# shellcheck disable=SC2086
set -- $INSTALLED
IFS=$OLD_IFS
for installed in "$@"; do
  [ -z "$installed" ] && continue
  INSTALLED_NORMALIZED="$(append_unique "$INSTALLED_NORMALIZED" "$(normalize_model "$installed")")"
done

echo "[ollama-entrypoint] ensuring models exist on shared volume (/root/.ollama)..."
OLD_IFS=$IFS
IFS='
'
# shellcheck disable=SC2086
set -- $MODELS
IFS=$OLD_IFS
for model in "$@"; do
  [ -z "$model" ] && continue
  if is_installed_model "$model"; then
    echo "[ollama-entrypoint] skip (already installed): ${model}"
    continue
  fi
  echo "[ollama-entrypoint] pull: ${model}"
  ollama pull "${model}"
done

echo "[ollama-entrypoint] models ready:"
ollama list || true
touch "${READY_STAMP}"

echo "[ollama-entrypoint] handing off to ollama serve (pid=${OLLAMA_PID})"
trap - EXIT INT TERM
wait "${OLLAMA_PID}"
