#!/usr/bin/env bash
# PreToolUse(Bash)。`git add -A` / `git add .` とその同等物を止める。
#
# なぜルールではなくフックなのか: 憲法は既にこれを禁じているが、
# **ルールが必要になるほどセッションが長いとき、ちょうどそのルールが薄れる。**
# 実際に、bulk add が他セッションの未コミットの仕事を巻き込み、
# 何をしたのか分からない横断コミットが生まれた。
# worktree を並行で使う以上、パスを明示するステージだけが安全な習慣。
# exit 2 = 拒否。stderr がそのままAIに返る。
#
# 既知の限界: 文字列として含むだけのコマンド（echo "git add -A"）も止まる。
# シェルを解析せず部分一致で見ているため。安全側に倒れるし回避も自明なので、
# パーサを書くほどの価値はない。
set -u
cmd="$(python3 -c 'import sys,json; d=json.load(sys.stdin); ti=d.get("tool_input") or d.get("toolInput") or {}; print(ti.get("command") or "")' 2>/dev/null)"
[ -z "$cmd" ] && exit 0

if printf '%s' "$cmd" | grep -qE '(^|[;&|]|\s)git\s+(-C\s+\S+\s+)?add\s+([^;&|]*\s)?(-A\b|--all\b|\.(\s|$)|\./(\s|$)|:/(\s|$))'; then
  reason="ブロック: bulk git add は使えません。自分のファイルはパスを明示してステージしてください（CLAUDE.md「仕事の進め方」）。bulk add は他セッションの未コミットの仕事を自分のコミットに巻き込みます。1ファイルずつ名前を挙げて実行し直してください: git add path/one.md path/two.md"
  echo "$reason" >&2
  python3 -c 'import json,sys; print(json.dumps({"decision":"deny","reason":sys.argv[1]}))' "$reason"
  exit 2
fi
exit 0
