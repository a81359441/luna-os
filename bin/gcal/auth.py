#!/usr/bin/env python3
"""カレンダー層の初回認証（OAuth）。一度だけ実行する。

OAuth 2.0 の installed-app フロー（PKCE つき）を走らせ、リフレッシュトークンを
`.secrets/gcal-token.json` に保存する（gitignore 対象）。OAuth クライアントは
`bin/gmail` と同じ Desktop クライアントを使い回すので、Google Cloud Console で
新しく作るものは無い。Calendar API を一度有効にするだけ。

**スコープは認可した瞬間にトークンへ焼き付く。** 下の SCOPES を書き換えても、
既にあるトークンには何の影響も無い。**トークンファイルを消してから**やり直すこと。

**このAIがカレンダーにどこまで触ってよいかは、既定ではなく本物の決定**なので、
3段階を用意してある:

    （既定）   readonly + app.created + events
               カレンダー全体を管理させる。自分で手で作った予定も含む。
               本物の予定を消さない保証は「構造」ではなく「振る舞い」になる
               （gcal.py は消す対象を声に出して名指しする）。
               Google 側もゴミ箱に30日間は残す。

    --narrow   readonly + app.created
               自分専用のレイヤだけを埋め、ほかは読むだけ。
               **自分で作った予定は構造的に編集も削除もできない。**
               スクリプトの行儀ではなく、トークンがそれを強制する。

    --readonly 読むだけ。何も書けない。

段階を変えるのは簡単: トークンファイルを消して、欲しいフラグで再実行する。

使い方:
    python3 bin/gcal/auth.py
    python3 bin/gcal/auth.py --narrow
    python3 bin/gcal/auth.py --readonly

標準ライブラリだけで動く。pip install は不要。
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
CLIENT_FILE = os.environ.get("GCAL_CLIENT", os.path.join(SECRETS, "gmail-client.json"))
TOKEN_FILE = os.environ.get("GCAL_TOKEN", os.path.join(SECRETS, "gcal-token.json"))

AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
PORT = 8765
REDIRECT = f"http://localhost:{PORT}/"

APP_CREATED = "https://www.googleapis.com/auth/calendar.app.created"
READONLY = "https://www.googleapis.com/auth/calendar.readonly"
EVENTS = "https://www.googleapis.com/auth/calendar.events"


def die(msg):
    print(f"\n✖ {msg}\n", file=sys.stderr)
    sys.exit(1)


def load_client():
    if not os.path.exists(CLIENT_FILE):
        die(
            f"OAuth クライアントのファイルがありません:\n    {CLIENT_FILE}\n\n"
            "bin/gmail が使うのと同じ Desktop クライアントです。bin/gmail/README.md の手順4を見てください。"
        )
    with open(CLIENT_FILE) as f:
        data = json.load(f)
    cfg = data.get("installed") or data.get("web")
    if not cfg:
        die("クライアントJSONに 'installed' も 'web' もありません。ファイルが違うのでは？")
    if "web" in data:
        print("⚠  これは『ウェブアプリケーション』のクライアントに見えます。**デスクトップアプリ**で作り直してください。")
    return cfg["client_id"], cfg["client_secret"]


class Catcher(http.server.BaseHTTPRequestHandler):
    """Google が返してくる1回のリダイレクトを受け止めるだけのサーバー。"""

    result = {}

    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        Catcher.result = {k: v[0] for k, v in q.items()}
        ok = "code" in Catcher.result
        body = (
            "<h2>認証できました。</h2><p>このタブを閉じて、ターミナルに戻ってください。</p>"
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
    if {"-h", "--help"} & set(sys.argv[1:]):
        # 保険。このスクリプトの主な作用はブラウザを開いてトークンを発行すること。
        # これが無いと `auth.py --help` が黙って認可まで進んでしまう。
        print(__doc__)
        return

    flags = {"--readonly", "--narrow"}
    unknown = [a for a in sys.argv[1:] if a not in flags]
    if unknown:
        die(f"知らない引数です: {' '.join(unknown)}\n使い方: auth.py [--narrow | --readonly]")

    if "--readonly" in sys.argv:
        scopes = [READONLY]
    elif "--narrow" in sys.argv:
        scopes = [READONLY, APP_CREATED]
    else:
        scopes = [READONLY, APP_CREATED, EVENTS]

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
            # select_account でアカウント選択を強制する。これが無いと Google は
            # ブラウザがログイン中のアカウントを黙って使い、カレンダーが
            # **意図しないアカウント**に作られる。
            "prompt": "consent select_account",
        }
    )

    server = http.server.HTTPServer(("localhost", PORT), Catcher)
    threading.Thread(target=server.handle_request, daemon=True).start()

    print("\n要求するスコープ:")
    for s in scopes:
        print(f"  · {s}")
    print(
        "\nブラウザを開きます。**自分のアカウント**でサインインしてください。\n"
        "Google は「確認されていないアプリ」と警告します。個人用の Desktop\n"
        "クライアントでは正常です。「詳細」→「（アプリ名）に移動」を押してください。\n"
        "同意画面のチェックは**全部**入れること。1つでも外すと書き込み権限が欠けます。\n"
    )
    print(f"ブラウザが開かないときは、このURLを貼ってください:\n\n{url}\n")
    webbrowser.open(url)

    deadline = time.time() + 300
    while not Catcher.result and time.time() < deadline:
        time.sleep(0.3)
    server.server_close()

    if not Catcher.result:
        die("5分待ちましたがリダイレクトが来ませんでした。")
    if "code" not in Catcher.result:
        die(f"Google がエラーを返しました: {Catcher.result}")
    if Catcher.result.get("state") != state:
        die("state が一致しません。中断します（CSRF の可能性）。")

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
        die("refresh_token が返りませんでした。myaccount.google.com/permissions でアプリの権限を取り消してから、やり直してください。")

    granted = tok.get("scope", "")
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": tok["refresh_token"],
        "access_token": tok.get("access_token"),
        "expires_at": time.time() + tok.get("expires_in", 3600) - 60,
        "scope": granted or " ".join(scopes),
    }
    with open(TOKEN_FILE, "w") as f:
        json.dump(payload, f, indent=2)
    os.chmod(TOKEN_FILE, 0o600)

    print(f"✔ トークンを保存しました: {TOKEN_FILE}（chmod 600、gitignore 対象の .secrets/ 内）")
    for s in scopes:
        if s not in granted:
            print(f"⚠  許可されなかったスコープ: {s}\n   同意画面のチェックを外した可能性があります。トークンを消して、やり直してください。")
    print("\n次:  python3 bin/gcal/gcal.py init")


if __name__ == "__main__":
    main()
