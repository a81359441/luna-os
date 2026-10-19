#!/usr/bin/env bash
# SessionStart フック。**この作業ツリー**で既に別のセッションが動いているときに警告する。
#
# なぜ必要か: 憲法は並行セッションごとに git worktree を分けることを求めている。
# 1つのチェックアウトを2つのウィンドウで開くと、作業ディレクトリも staging index も
# HEAD も共有される。実害は2回とも実際に起きている:
# 片方の未コミットのファイルがもう片方のコミットに巻き込まれ、
# 作業中に HEAD が動き、ブランチのポインタが迷子になった。
#
# ルールは文章としては2回書かれ、2回とも守られなかった。
# **2つ目のウィンドウを開くほど忙しいときに、文章は真っ先に消える。**
# だからここで、見ないことができない形にする。
#
# 「生きている」の定義 = WINDOW_MIN 分以内に触られ、**かつ所有プロセスがまだ
# 動いている**マーカー（判定は session-pid-lib.sh）。時刻を正直に保つのが
# session-heartbeat.sh。
#
# プロセスの確認を入れる前は、`/clear` のたびに誤って警告が出ていた。
# `/clear` は同じプロセスのまま新しいセッション ID を発行するので、直前の
# マーカーが新鮮なまま残るからだ。**何も起きていないのに鳴る警告は、
# 読み飛ばす習慣を作る。** このフックが唯一失ってはいけないものがそれ。
#
# 何もブロックしない。出力して exit 0 するだけ。
set -u
WINDOW_MIN=45

dir="${CLAUDE_PROJECT_DIR:-.}/.session"
[ -d "$dir" ] || exit 0

# shellcheck source=session-pid-lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/session-pid-lib.sh"

self="${CLAUDE_CODE_SESSION_ID:-${GROK_SESSION_ID:-}}"
self_pid="$(agent_pid)"

others=0
names=""
while IFS= read -r f; do
  [ -n "$f" ] || continue
  sid="$(basename "$f")"
  sid="${sid#domain-}"
  [ -n "$self" ] && [ "$sid" = "$self" ] && continue

  # 所有プロセスが分かるなら、それで決める。pid ファイルがないマーカーは
  # この仕組みより前のものか、プロセスを特定できなかったハーネスのものなので、
  # 従来どおり更新時刻だけで判定する。
  pidfile="$dir/pid-$sid"
  if [ -e "$pidfile" ]; then
    owner="$(cat "$pidfile" 2>/dev/null)"
    # 自分と同じプロセス = 同じウィンドウの1つ前のセッション ID。
    # `/clear` や再開であって、2つ目のウィンドウではない。
    [ -n "$self_pid" ] && [ "$owner" = "$self_pid" ] && continue
    pid_is_agent "$owner" || continue
  fi

  others=$((others + 1))
  names="$names  ${sid}
"
done <<< "$(find "$dir" -maxdepth 1 -name 'domain-*' -mmin "-$WINDOW_MIN" 2>/dev/null)"

[ "$others" -eq 0 ] && exit 0

topic="$(date +%m%d)"
# worktree の置き場所は絶対パスで出す。親フォルダから開いていると `../` は親の
# リポジトリに効いてしまう（2026-10-19、ある利用者の環境で親の worktree が切られた）。
# 親フォルダ自体が git リポジトリなら、その未追跡ファイルに出ないよう一段外に置く。
root="$(cd "${CLAUDE_PROJECT_DIR:-.}" && pwd)"
base="$(dirname "$root")"
git -C "$base" rev-parse --is-inside-work-tree >/dev/null 2>&1 && base="$(dirname "$base")"
wt="$base/$(basename "$root")-<topic>"
cat <<MSG
⚠️  並行セッション: 直近 ${WINDOW_MIN} 分に、他の ${others} セッションがこのチェックアウトを触っています。

触られたマーカー（UUID でない名前は、生きているセッションとは限らない残骸です）:
$names
作業ディレクトリも git の index も HEAD も共有しています。リポジトリを触る前に
必ずユーザーに伝えてください。実際に起きた結果: 他セッションの未コミットのファイルが
自分のコミットに巻き込まれる、作業中に HEAD が動く、ブランチのポインタが迷子になる。

安全な選択肢（上から順に）:
  1. 直列化する。もう一方のウィンドウを先に終わらせる。
  2. 自分の worktree を切る:  git -C "$root" worktree add "$wt" -b work/<topic>-${topic}
     そこで作業し、締めで main にマージしてから: git -C "$root" worktree remove "$wt"
     鍵（.secrets）と状態（.session）は道具が本体から読むので、複製しなくてよい。

どちらにせよ: ステージはパスを明示して行うこと。bulk add は使わない（フックで禁止済み）。
MSG
exit 0
