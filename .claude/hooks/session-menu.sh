#!/usr/bin/env bash
# SessionStart(startup): スタートメニュー（ブリーフ）を出す。
# bin/menu が決定的に描くので、モデルはこれを作るのにトークンを1つも使わない。
# セッション開始を失敗させないこと。何があっても exit 0。
set -u
ROOT="${CLAUDE_PROJECT_DIR:-${GROK_WORKSPACE_ROOT:-$PWD}}"
[ -x "$ROOT/bin/menu" ] || exit 0
python3 "$ROOT/bin/menu" --no-color 2>/dev/null || true
exit 0
