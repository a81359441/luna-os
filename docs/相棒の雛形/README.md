# {{SEAM_NAME}}

相棒 {{COMPANION_NAME}} と、運用AI {{AI_NAME}} の間のつなぎ目です。私的リポジトリ。本人以外は入れません。

## 二つのフォルダ

| フォルダ | 書く | 読む | 中身 |
|---|---|---|---|
| `feed/` | {{AI_NAME}} | {{COMPANION_NAME}} | {{AI_NAME}} が置く荷物 |
| `inbox/` | {{COMPANION_NAME}} | {{AI_NAME}} | 本人との会話で {{COMPANION_NAME}} が拾ったこと |

## feed（{{AI_NAME}} → {{COMPANION_NAME}}）

- **`feed/now.md`**: 今日の3つ、予定、`ask:` の1行。{{AI_NAME}} が `bin/companion-now` で作る（ブリーフのデータから、AI を使わない）。先頭の時刻は中身を作った時刻なので、古いままなら古いと分かる。
- **話題ごとの feed**: `feed/YYYY-MM-DD-<題>.md`。終わったら {{AI_NAME}} が `feed/done/` に移す。
- 🔒 の項目は件数だけ。

{{COMPANION_NAME}} はこれを自分の言葉で、本人にとっての意味として届ける。丸写しはしない。今日を自分で計算し直さない。

## inbox（{{COMPANION_NAME}} → {{AI_NAME}}）

- 一件一ファイル: `inbox/YYYY-MM-DD-HHMM-<題>.md`。
- 書くこと: 本人が言ったこと（本人の言葉のまま）、{{COMPANION_NAME}} の推測（「推測:」と分けて）、どの feed のどの項目への返事か。
- 種類: `予定の変更` `メモ` `区切りの結果（できた / ずれた / 記録だけ）`。
- {{AI_NAME}} はこれを材料として読む。正本への反映は本人の承認のあと。
- **カレンダーはどちらも直してよい。条件は2つ。** (1) 直す前に、カレンダーの最新とこのリポジトリの最新（`git pull`）を両方見る。(2) {{COMPANION_NAME}} が直したら、同じ回のうちに inbox に「予定の変更」として残す。
- 処理したものは {{AI_NAME}} が `inbox/done/` に移す。

## 書かないもの

パスワード、トークン、メール本文、日記の丸写し、他人の連絡先。
