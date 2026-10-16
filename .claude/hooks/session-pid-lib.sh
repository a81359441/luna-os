#!/usr/bin/env bash
# session-marker.sh と parallel-session-warn.sh が共有する部品。
#
# 「セッションが生きている」の判定は、もともと「マーカーの更新時刻が新しい」
# だった。これは2つの場面で嘘をつく。
#
#   1. `/clear` は**同じプロセスのまま**新しいセッション ID を発行する。
#      直前のマーカーが新鮮なまま残るので、ウィンドウは1つしか開いていないのに
#      「もう1つ動いている」と読めてしまう。
#   2. 閉じたウィンドウのマーカーも、45分間は「生きている」ふりをし続ける。
#
# そこで判定を「そのマーカーを所有するプロセスがまだ動いているか」に変えた。
# 更新時刻は「そのセッションが最後に喋った時刻」を言うだけのものになる。
#
# このファイルは読み込まれる（source）専用。単体では実行しない。

# このフックの親をたどって、フックを起動したエージェントのプロセス ID を探す。
# 見つかれば表示し、見つからなければ何も出さない。
# 何も出さなかった場合、呼び出し側は従来どおり更新時刻だけで判定する
# （つまり、分からないときは警告する側に倒れる）。
agent_pid() {
  local pid comm i
  pid=$$
  for i in 1 2 3 4 5 6 7 8; do
    comm="$(ps -o comm= -p "$pid" 2>/dev/null)"
    [ -n "$comm" ] || return 0
    case "$comm" in
      *claude*|*grok*|*codex*) printf '%s\n' "$pid"; return 0 ;;
    esac
    pid="$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')"
    [ -n "$pid" ] || return 0
    [ "$pid" -le 1 ] 2>/dev/null && return 0
  done
}

# $1 が生きているエージェントのプロセスなら真。
# プロセス ID は使い回されるので、番号だけでなくコマンド名まで確認する。
# そうしないと、終わったセッションが無関係の新しいプロセスとして蘇る。
pid_is_agent() {
  local comm
  [ -n "${1:-}" ] || return 1
  comm="$(ps -o comm= -p "$1" 2>/dev/null)" || return 1
  case "$comm" in
    *claude*|*grok*|*codex*) return 0 ;;
  esac
  return 1
}
