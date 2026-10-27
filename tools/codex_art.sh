#!/usr/bin/env bash
# Картинка через ChatGPT (встроенный image_gen в Codex CLI), с картинкой-образцом стиля.
# usage: tools/codex_art.sh <имя> <файл с текстом задания> [образец.png]  ->  art-src/raw/<имя>.png
set -u
NAME="$1"; PROMPT_FILE="$2"; REF="${3:-}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CODEX=$(ls -t "$HOME"/AppData/Local/OpenAI/Codex/bin/*/codex.exe 2>/dev/null | head -1)
OUT="$ROOT/art-src/raw"; mkdir -p "$OUT" "$ROOT/art-src/logs"
LOG="$ROOT/art-src/logs/$NAME.log"
PROMPT="Use your built-in image_gen tool to create exactly ONE image. Do not run shell commands or write code; just call image_gen once and then reply 'done'.

$(cat "$PROMPT_FILE")"
ARGS=()
[ -n "$REF" ] && ARGS=(-i "$REF")
for attempt in 1 2; do
  "$CODEX" exec --skip-git-repo-check -s read-only -m "${CODEX_MODEL:-gpt-5.6-terra}" -c model_reasoning_effort="low" "$PROMPT" "${ARGS[@]}" </dev/null >"$LOG" 2>&1
  SID=$(grep -m1 -o 'session id: [0-9a-f-]*' "$LOG" | cut -d' ' -f3)
  F=$(ls -t "$HOME/.codex/generated_images/$SID"/*.png 2>/dev/null | head -1)
  if [ -n "$F" ]; then cp "$F" "$OUT/$NAME.png"; echo "ok $NAME"; exit 0; fi
  grep -q "usage limit" "$LOG" && { echo "limit"; exit 1; }
  echo "retry $NAME ($attempt)"
done
echo "FAIL $NAME"; exit 1
