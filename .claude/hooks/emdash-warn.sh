#!/usr/bin/env bash
# PostToolUse(Write|Edit)。今の編集でダッシュを書いてしまったときに警告する。
#
# ダッシュ禁止は絶対のルールで、実運用でいちばん多く破られるルールでもある。
# 編集自体はもう書かれているので、これは何もブロックしない。
# **意図がまだ新しいうちに**、直すべき行をそのまま手渡すためのもの。
set -u
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
[ -x bin/lint ] || exit 0

out="$(NO_COLOR=1 bin/lint --emdash-only 2>/dev/null)"
if [ -n "$out" ]; then
  {
    echo "ダッシュを書きました（ハードルール: どの言語・どのドメインでも禁止）。後回しにせず、今この場で文を組み直してください:"
    printf '%s\n' "$out"
  } >&2
  exit 2
fi
exit 0
