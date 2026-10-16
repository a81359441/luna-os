# Gmail リーダー（読み取り専用）

自分のGmailからメールを取り出して、AIに読ませ、整理させるための道具です。請求のやり取り、問い合わせ、取引先とのスレッド。人間が読み返すには多すぎるものを、機械に読ませて要点だけ残します。

**構造として読み取り専用。** `gmail.py` には送信・下書き・削除・ラベル変更のコードが1行もありません。投げるのはすべてGmail APIへのGETです。`auth.py` が既定で発行するトークンも `gmail.readonly` だけなので、バグがあってもメールを送ることはできません。

## 境界のルール（重要）

メールは**揮発する情報（Connections）**です。取り出した生のメールは `data/` や作業用ディレクトリ（どちらもgitignore対象）に置き、**コミットされる知識ファイルには入れません**。

索引に入れてよいのは、そこから**コンパイルされた、個人情報を含まない事実**だけです（取引の条件、単価の相場、やり取りのパターン）。生のメールをそのまま `knowledge/` に置くと、他人の個人情報を自分のリポジトリに永久保存することになります。

## 初回のセットアップ（15分ほど・ブラウザ作業）

OAuthクライアントを置くためのGoogle Cloudプロジェクトが要ります。無料です。

1. **console.cloud.google.com** → 上部のプロジェクト選択 → **新しいプロジェクト**。名前は何でもよい（例 `my-gmail-reader`）→ 作成 → そのプロジェクトが選択されていることを確認。

2. **Gmail APIを有効にする**：検索バーに「Gmail API」→ **有効にする**。

3. **同意画面を設定する**：*APIとサービス → OAuth同意画面*（新しいコンソールでは *Google Auth Platform → ブランディング / 対象*）。
   - ユーザーの種類: **外部**
   - アプリ名: 何でもよい。サポートメール: 自分のアドレス
   - **対象 → テストユーザー → 追加 → 読み込みたいGmailアドレスを追加する。** ここを飛ばすと、次の承認が必ず拒否されます。
   - 審査への申請は不要です。

4. **クライアントを作る**：*APIとサービス → 認証情報 → 認証情報を作成 → OAuthクライアントID → アプリケーションの種類: **デスクトップアプリ*** → 作成 → **JSONをダウンロード**。

   ダウンロードしたファイルを、このパスに置きます。

   ```
   .secrets/gmail-client.json
   ```

   `.secrets/` はgitignore対象なので、この端末から出ることはありません。

5. **認証する**：

   ```bash
   python3 bin/gmail/auth.py
   ```

   ブラウザが開きます。**読み込みたいアカウントでログインしてください。** 「このアプリはGoogleで確認されていません」という警告が出ますが、個人用のデスクトップクライアントでは正常です。**詳細 → <アプリ名>（安全ではないページ）に移動** で進み、読み取り専用のアクセスを許可します。

6. **確認する**：

   ```bash
   python3 bin/gmail/gmail.py whoami
   ```

   自分のアドレスとメール数が出れば成功です。

## 使い方

```bash
python3 bin/gmail/gmail.py whoami
python3 bin/gmail/gmail.py labels
python3 bin/gmail/gmail.py search "請求書" --max 50
python3 bin/gmail/gmail.py search "newer_than:1y (お問い合わせ OR ご依頼)"
python3 bin/gmail/gmail.py show 18f2c9a1b3d4e5f6
python3 bin/gmail/gmail.py dump "請求書 newer_than:1y" --max 40 --out data/gmail/corpus.md
python3 bin/gmail/gmail.py screen "newer_than:6m ご依頼" --brands "BrandA,BrandB"
```

Gmailの検索構文がそのまま使えます：`from:`、`newer_than:6m`、`has:attachment`、`-in:chats`、`subject:`、`OR`、括弧。

`dump` は1通ずつmarkdownで書き出します（ヘッダ＋本文＋添付ファイル名）。AIが実際に読んでコンパイルするのはこのファイルです。ここまで来たら、AIにこう頼めます。

> `data/gmail/corpus.md` を読んで、単価と条件のパターンを `domains/<ドメイン>/index.md` にまとめて。生のメールは索引に入れないで。

`screen` は差出人のリスクを機械的に洗い出します（フリーメールなのに企業を名乗る、返信先が別ドメイン、金銭や身分証の要求など）。**順位づけをするだけで、判断はしません。** 相手を判断するのは人間です。

## つまずきやすいところ

- **テスト状態だとトークンが7日で切れる。** 未審査のアプリでは、Googleは機微なスコープのリフレッシュトークンを7日で失効させます。`invalid_grant` が出たら `python3 bin/gmail/auth.py` をもう一度実行するだけ（20秒）。恒久化したいなら *OAuth同意画面 → **アプリを公開***。未確認の警告は残りますが、トークンは切れなくなります。
- **スコープは認証時に焼き付く。** コンソールでスコープを変えても既存のトークンには反映されません。`.secrets/gmail-token.json` を消して `auth.py` をやり直します。
- **アカウントを間違えた。** `whoami` が別のアドレスを返したら、意図しないアカウントで承認しています。myaccount.google.com/permissions で解除し、トークンファイルを消してやり直してください。
- **下書きを書かせたくなったら。** `python3 bin/gmail/auth.py --compose` でスコープを足せます。ただしGoogleの compose スコープは**送信も許可します**（下書き専用のスコープは存在しません）。その時点から「送れない」保証は、スクリプトに送信のコードが無いことに依存します。本当に必要になるまで足さないこと。
- **受信箱を片付けさせたくなったら。** `python3 bin/gmail/auth.py --modify` で gmail.modify を足すと、`bin/gmail/tidy.py` でアーカイブ・既読・ラベル付けができます。**既定はドライラン**（何に触るかを送信者ごとに数えて止まる）で、`--apply` を付けたときだけ実行します。実行した内容は `.session/gmail-tidy.log` に残り、`python3 bin/gmail/tidy.py unarchive --apply` で直前のアーカイブを戻せます。送信・ゴミ箱・削除のコードはありません。アーカイブは受信トレイから外すだけで、メールは「すべてのメール」に残ります。

## やめるとき

いつでも切れます。myaccount.google.com/permissions からアプリを削除。ローカルは `rm -rf .secrets/` で消えます。
