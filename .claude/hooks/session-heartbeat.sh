#!/usr/bin/env bash
# UserPromptSubmit フック。このセッションのマーカーファイルを touch して、
# mtime を「本当に生きているか」の信号にする。
#
# これが無いと、マーカーの時刻は「最後にドメインを切り替えた時刻」でしかなく、
# 忙しいセッションが死んで見え、閉じたセッションが生きて見える。
# parallel-session-warn.sh はこの時刻を読むので、それを真実にするのがここ。
# 何も出力しない。文脈にノイズを足してはいけない。
#
# pid ファイルがまだ無ければ、ここでマーカーの所有者も名乗る。`/clear` の後は
# SessionStart の環境変数に直前の ID が残っていることがあり、新しい ID の
# マーカーはここで初めて作られる。所有者の無いマーカーは parallel-session-warn.sh
# で更新時刻だけの判定に落ちる（分からないときは警告する側に倒れる）。
set -u
dir="${CLAUDE_PROJECT_DIR:-.}/.session"
sid="$(python3 -c 'import sys,json; print(json.load(sys.stdin).get("session_id",""))' 2>/dev/null)"
[ -z "$sid" ] && sid="${CLAUDE_CODE_SESSION_ID:-}"
[ -z "$sid" ] && exit 0
mkdir -p "$dir" 2>/dev/null
[ -e "$dir/domain-$sid" ] || : > "$dir/domain-$sid" 2>/dev/null
touch "$dir/domain-$sid" 2>/dev/null
if [ ! -e "$dir/pid-$sid" ]; then
  # shellcheck source=session-pid-lib.sh
  . "$(dirname "${BASH_SOURCE[0]}")/session-pid-lib.sh"
  pid="$(agent_pid)"
  [ -n "$pid" ] && printf '%s\n' "$pid" > "$dir/pid-$sid" 2>/dev/null
fi
exit 0
