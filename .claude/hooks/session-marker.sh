#!/usr/bin/env bash
# SessionStart: このセッションのドメインマーカーを空に戻し、
# そのマーカーを所有するプロセスを記録する（理由は session-pid-lib.sh）。
# Claude Code は CLAUDE_CODE_SESSION_ID をフック環境に出す。
# Grok Build は GROK_SESSION_ID を出すので、`.session/grok-session-id` にも
# 書いておく（エージェント側の bash には GROK_SESSION_ID が渡らないため）。
set -u
ROOT="${CLAUDE_PROJECT_DIR:-${GROK_WORKSPACE_ROOT:-$PWD}}"
DIR="$ROOT/.session"
mkdir -p "$DIR"

# shellcheck source=session-pid-lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/session-pid-lib.sh"

SID="${CLAUDE_CODE_SESSION_ID:-}"
if [ -z "$SID" ]; then
  SID="${GROK_SESSION_ID:-}"
fi

if [ -n "$SID" ]; then
  : > "$DIR/domain-$SID"
  AGENT_PID="$(agent_pid)"
  if [ -n "$AGENT_PID" ]; then
    printf '%s\n' "$AGENT_PID" > "$DIR/pid-$SID"
  else
    rm -f "$DIR/pid-$SID" 2>/dev/null || true
  fi
fi

if [ -n "${GROK_SESSION_ID:-}" ]; then
  printf '%s\n' "$GROK_SESSION_ID" > "$DIR/grok-session-id"
fi

# 所有プロセスが既に終わっているマーカーを片付ける。
# これがあるおかげで、閉じたウィンドウが次のウィンドウに45分つきまとわない。
# pid ファイルがないマーカーは、この仕組みより前のものか、プロセスを特定できな
# かったハーネスのものなので、下の日数による掃除に任せる。
for P in "$DIR"/pid-*; do
  [ -e "$P" ] || continue
  DEAD_SID="${P##*/pid-}"
  if ! pid_is_agent "$(cat "$P" 2>/dev/null)"; then
    rm -f "$P" "$DIR/domain-$DEAD_SID" "$DIR/meter-$DEAD_SID" 2>/dev/null || true
  fi
done

find "$DIR" -name 'domain-*' -mtime +1 -delete 2>/dev/null || true
find "$DIR" -name 'pid-*' -mtime +1 -delete 2>/dev/null || true
rm -f "$DIR/domain" 2>/dev/null || true
exit 0
