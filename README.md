# LineBridge MCP

自分の LINE（個人トーク・グループ・OpenChat）を AI の MCP クライアントから扱う、非公式の個人向け MCP サーバーです。Windows 11 x64 と Ubuntu 24.04 x86_64 に対応します。

## 導入

このリポジトリを clone するか、GitHub の「Code → Download ZIP」でソースを取得します。

```sh
git clone https://github.com/nikkei225gp/line-bridge-mcp.git
cd line-bridge-mcp
```

- Windows: `LineBridge.cmd` を開き、`1 Install` を選びます。[Windows 導入手順](START-HERE.md)
- Ubuntu: `bash ./LineBridge.sh` を実行し、`1 Install` を選びます。[Linux 導入手順](START-HERE-LINUX.md)

Python・Deno・linejs などの固定依存物は初回導入時に取得します。管理者権限や sudo は不要です。

## MCP の登録とログイン

インストール後、次のコマンドが出力する `command` と `args` を、ローカル stdio を実行できる MCP クライアントへ登録します。登録名は `line` です。

```powershell
# Windows
.\scripts\linebridge.ps1 clients --format mcp
```

```sh
# Ubuntu
bash scripts/linebridge.sh clients --format mcp
```

`--format codex` と `--format opencode` も使えます。クライアント設定は自動変更しません。

接続後は `bridge_status` → `login_start` → `login_status` の順で進めます。新規の既定端末は `ANDROIDSECONDARY` です。表示された QR を本人の LINE で読み取り、必要時は PIN を入力します。通常 LINE と OpenChat は1回の認証を共有します。メニューの `2 QR login` でもログインできます。

## できること

- 通常 LINE・グループ・OpenChat の一覧、メッセージ取得、保存済み本文の検索
- テキスト送信、引用リプライ、OpenChat スレッドの読み書き
- 写真の取得（6 MiB まで）、メンバーの表示名の取得
- 自分の送信の取り消し、OpenChat の権限に応じた投稿削除
- 明示的に有効にしたチャットの定期監視と、任意の webhook 通知

既読は付けません。リアクション・チャットへの参加・動画/音声/ファイルの取得には対応しません。履歴は取得できる範囲に限られ、全履歴や最新 N 件を保証しません。

## 既定の動作と保存

新規プロファイルでは**送信が有効で、送信・取り消し・削除ごとの確認画面はありません**。AI クライアント側で操作を管理してください。送信を使わない場合は、停止中に `sending disable` を実行します。監視と外部通知は初期状態では無効です。

認証・鍵・本文は利用者の PC のプロファイルへ保存します。Windows は DPAPI、Linux は AES-256-GCM と本人用ファイル権限を使います。取得結果や QR/PIN は、利用している AI サービスへ渡る場合があります。保存先全体・認証ファイル・チャット履歴を共有しないでください。

## 更新

ブリッジを停止し、保存先を安全にバックアップしてから更新します。

```sh
git pull --ff-only origin main
```

依存物の更新手順と起動方法は [利用手順](docs/USER-GUIDE.md) を参照してください。

LINE の非公式な連携です。LINE 側の変更で使えなくなることや、アカウントが制限される可能性があります。

詳しくは [利用手順](docs/USER-GUIDE.md)、[保存と制限](docs/SECURITY-AND-LIMITS.md)、[検証](docs/TEST-STATUS.md)、[対応候補](BOARD.md) を参照してください。本体は [MIT License](LICENSE) です。依存物は [第三者表記](THIRD-PARTY-NOTICES.md) を参照してください。
