#!/usr/bin/env bash
# UserPromptSubmit フック。今いるドメインの「スタンス」（そのドメインで AI が
# ユーザーとどう一緒に考えるか）を毎ターン注入する。一度言っただけのルールは
# 文脈が伸びると薄れる、という slow-mode.sh と同じ理由。
#
# 今のドメイン = このセッションの .session/domain-<sid> の1行目。
# スタンス = knowledge/domains/<slug>/README.md の `## Stance` 節
# （親/子 形式のサブドメインも解決する）。
# 最大5行。ドメイン・README・スタンスのどれかが無ければ何も出さない。
# 発言を止めることはしない。標準出力がそのまま文脈になる。
set -u
root="${CLAUDE_PROJECT_DIR:-.}"
sid="$(python3 -c 'import sys,json; print(json.load(sys.stdin).get("session_id",""))' 2>/dev/null)"
[ -z "$sid" ] && sid="${CLAUDE_CODE_SESSION_ID:-}"
[ -z "$sid" ] && exit 0
marker="$root/.session/domain-$sid"
[ -f "$marker" ] || exit 0
slug="$(head -n 1 "$marker" | tr -d '[:space:]')"
[ -z "$slug" ] && exit 0
case "$slug" in *..*) exit 0 ;; esac
readme="$root/knowledge/domains/$slug/README.md"
[ -f "$readme" ] || exit 0
stance="$(awk '
  /^## Stance[[:space:]]*$/ { on = 1; next }
  on && /^## / { exit }
  on && NF { print; n++ }
  n >= 5 { exit }
' "$readme")"
[ -z "$stance" ] && exit 0
printf '🧭 %s のスタンス（ドメインの README より、毎ターン再注入）:\n%s\n' "$slug" "$stance"
exit 0
