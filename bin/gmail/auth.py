#!/usr/bin/env python3
"""Gmail の一度きりのOAuth認証。

OAuth 2.0 のインストール済みアプリ・フロー（PKCE付き）をGoogleに対して実行し、
得られたリフレッシュトークンを .secrets/gmail-token.json に保存する（gitignore対象）。

**スコープは認証した時点でトークンに焼き付く。** 下の SCOPES を書き換えても
既存のトークンは変わらない。変えたいときはトークンファイルを消して実行し直すこと。

既定は読み取り専用（意図的）。メールを読んで整理することはできるが、
このAIが何かを作成したり送信したりすることはできない。下書きを作らせたくなったら:

    python3 bin/gmail/auth.py --compose

これで gmail.compose が加わる。受信箱の整理（アーカイブ・既読・ラベル。bin/gmail/tidy.py）も
させたいなら --modify で gmail.modify が加わる。ただしGoogleの compose スコープは**送信も許可する**。
その時点からは「送れない」保証はスコープではなく、gmail.py に送信のコードパスが
無いこと（grepすれば分かる）に依存することになる。読み取り専用にはその信頼が要らない。

使い方:
    python3 bin/gmail/auth.py            # 読み取り専用（既定）
    python3 bin/gmail/auth.py --compose  # 読み取り＋下書き作成
    python3 bin/gmail/auth.py --modify   # 読み取り＋受信箱の整理（--compose と併用可）

標準ライブラリのみ。pip install は不要。
"""
import base64
import hashlib
import http.server
import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def main_checkout(repo):
    """worktree の中でも、鍵と状態は本体のチェックアウトにある。

    `.secrets/` と `.session/` は gitignore 済みなので、新しい worktree には来ない。
    worktree の `.git` はファイルで、`gitdir: <本体>/.git/worktrees/<名前>` を指す。
    そこから本体を割り出す（2026-10-19、worktree でブリーフがカレンダーに届かなかった）。
    """
    g = os.path.join(repo, ".git")
    if os.path.isfile(g):
        with open(g, encoding="utf-8") as f:
            line = f.read().strip()
        if line.startswith("gitdir:"):
            gd = os.path.normpath(os.path.join(repo, line[len("gitdir:"):].strip()))
            if os.path.basename(os.path.dirname(gd)) == "worktrees":
                return os.path.dirname(os.path.dirname(os.path.dirname(gd)))
    return repo


HOME = main_checkout(REPO)
SECRETS = os.path.join(HOME, ".secrets")
CLIENT_FILE = os.environ.get("GMAIL_CLIENT", os.path.join(SECRETS, "gmail-client.json"))
TOKEN_FILE = os.environ.get("GMAIL_TOKEN", os.path.join(SECRETS, "gmail-token.json"))

AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
PORT = 8765
REDIRECT = f"http://localhost:{PORT}/"

READONLY = "https://www.googleapis.com/auth/gmail.readonly"
COMPOSE = "https://www.googleapis.com/auth/gmail.compose"
MODIFY = "https://www.googleapis.com/auth/gmail.modify"


def die(msg):
    print(f"\n✖ {msg}\n", file=sys.stderr)
    sys.exit(1)


def load_client():
    if not os.path.exists(CLIENT_FILE):
        die(
            f"OAuthクライアントのファイルがありません:\n    {CLIENT_FILE}\n\n"
            "Google Cloud Console（APIとサービス → 認証情報 → デスクトップアプリの\n"
            "OAuthクライアント → JSONをダウンロード）から取得して、上のパスに保存してください。\n"
            "手順は bin/gmail/README.md のステップ4。"
        )
    with open(CLIENT_FILE) as f:
        data = json.load(f)
    cfg = data.get("installed") or data.get("web")
    if not cfg:
        die("クライアントJSONに 'installed' も 'web' もありません。ファイルが違う可能性があります。")
    if "web" in data:
        print("⚠  「ウェブアプリケーション」のクライアントに見えます。**デスクトップアプリ**で作り直してください。")
    return cfg["client_id"], cfg["client_secret"]


class Catcher(http.server.BaseHTTPRequestHandler):
    """Googleが返してくるリダイレクトを1回だけ受け取る。"""

    result = {}

    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        Catcher.result = {k: v[0] for k, v in q.items()}
        ok = "code" in Catcher.result
        body = (
            "<h2>認証できました。</h2><p>このタブを閉じてターミナルに戻ってください。</p>"
            if ok
            else f"<h2>失敗しました。</h2><pre>{Catcher.result}</pre>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *a):
        pass  # ターミナルを汚さない


def post_form(url, fields):
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        die(f"トークンの交換に失敗しました ({e.code}):\n{e.read().decode()}")


def main():
    scopes = [READONLY]
    if "--compose" in sys.argv:
        scopes.append(COMPOSE)
    if "--modify" in sys.argv:
        scopes.append(MODIFY)

    client_id, client_secret = load_client()
    os.makedirs(SECRETS, exist_ok=True)

    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    state = secrets.token_urlsafe(16)

    url = AUTH_URI + "?" + urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "scope": " ".join(scopes),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "access_type": "offline",
            # select_account でアカウント選択画面を強制する。これが無いとGoogleは
            # ブラウザで既にログイン中のアカウントを黙って使い、意図しない
            # メールボックスのトークンができてしまう。
            "prompt": "consent select_account",
        }
    )

    server = http.server.HTTPServer(("localhost", PORT), Catcher)
    threading.Thread(target=server.handle_request, daemon=True).start()

    print("\n要求するスコープ:")
    for s in scopes:
        print(f"  · {s}")
    print(
        "\nブラウザを開きます。**読み込みたいメールが届くアカウント**でログインしてください。\n"
        "「このアプリはGoogleで確認されていません」という警告が出ますが、個人用の\n"
        "デスクトップクライアントでは正常です。詳細 → <アプリ名>（安全ではないページ）に移動。\n"
    )
    print(f"ブラウザが開かない場合は、このURLを貼ってください:\n\n{url}\n")
    webbrowser.open(url)

    deadline = time.time() + 300
    while not Catcher.result and time.time() < deadline:
        time.sleep(0.3)
    server.server_close()

    if not Catcher.result:
        die("5分待ちましたがリダイレクトが返りませんでした。")
    if "code" not in Catcher.result:
        die(f"Googleがエラーを返しました: {Catcher.result}")
    if Catcher.result.get("state") != state:
        die("stateが一致しません。中断します（CSRFの可能性）。")

    tok = post_form(
        TOKEN_URI,
        {
            "code": Catcher.result["code"],
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": REDIRECT,
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        },
    )
    if "refresh_token" not in tok:
        die("refresh_token が返りませんでした。myaccount.google.com/permissions でアプリを削除してからやり直してください。")

    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": tok["refresh_token"],
        "access_token": tok.get("access_token"),
        "expires_at": time.time() + tok.get("expires_in", 3600) - 60,
        "scope": tok.get("scope", " ".join(scopes)),
    }
    with open(TOKEN_FILE, "w") as f:
        json.dump(payload, f, indent=2)
    os.chmod(TOKEN_FILE, 0o600)

    print(f"✔ トークンを保存しました: {TOKEN_FILE}（chmod 600、gitignore対象の .secrets/ 内）")
    print("\n次:  python3 bin/gmail/gmail.py whoami")


if __name__ == "__main__":
    main()
