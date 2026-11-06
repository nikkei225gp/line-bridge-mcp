# LineBridge 0.3.0 Ubuntu 24.04 LTSデスクトップ向け

対象はUbuntu 24.04 LTSのデスクトップ版、x86_64です。自分の通常LINEと参加済みOpenChatを、Codex / OpenCode V2から読み取る非公式ツールです。実行環境と認証情報は、自分のLinux PCに作ります。

## 導入

1. ソースを自分だけが使うローカルのフォルダに置きます。ホームフォルダ内がおすすめです。共有フォルダやWindowsとの共有マウントへ置かないでください。最新版のソースを使用してください。
2. 展開した `line-bridge` フォルダをターミナルで開き、`bash ./LineBridge.sh` を実行します。管理者として実行せず、sudoを付けません。
3. メニューの`1 Install`を選びます。固定したuv・Python 3.11・Deno・linejsを取得し、ハッシュとロックファイルを照合します。Ubuntuに含まれる`python3`があればuvの事前導入は不要です。
4. `2 QR login (window)` を選びます。専用画面のQRを、自分のスマートフォンのLINEで読み取り、承認します。PINが出たら自分のスマートフォンで入力します。通常LINEとOpenChatは共通の1回の認証です。QR・PIN・認証情報を公開の場所や第三者へ送らないでください。AI に表示した場合はそのサービスへ渡ります。
5. `3 Start` → `4 Status`。`6 Codex config` または `7 OpenCode V2 config` に表示される、自分のPC用の設定をクライアントへ追加します。既存の他の設定を残してください。Codexの場合は `~/.codex/config.toml` の `[mcp_servers.line]` です。同名登録は重複させず、その登録だけを更新します。
6. 新しいローカル会話で「LINEのbridge_statusを確認して、参加済みOpenChatを一覧して」と依頼し、読む部屋を選びます。終了時はメニューの `5 Stop` を選びます。

次回は `bash ./LineBridge.sh` → `3 Start`。PCへの自動起動登録は行いません。メニューを閉じても起動したブリッジは残ります。

新規プロファイルのログイン端末は Android サブ端末が既定です。iPad・Windows・Mac も選択できます。自分で起動した認証だけを承認してください。

QR画面が使えない場合は `9 Terminal QR` で、自分のターミナルにQRを表示します。端末を十分広くしてください。端末表示は対話端末でのみ許可し、ログ保存やAIからの非対話実行では認証を開始しません。

## コマンド操作

```bash
bash scripts/install.sh
bash scripts/linebridge.sh login
bash scripts/linebridge.sh start
bash scripts/linebridge.sh clients --format codex
bash scripts/linebridge.sh status
bash scripts/linebridge.sh stop
```

実際の認証切れを確認したときだけ、停止して `bash scripts/linebridge.sh login --replace` を本人が実行し、再起動します。

## AIからの使い方

最初に「LINEのbridge_statusを確認して」と依頼します。通常LINEは `personal_chats`、OpenChatは `openchat_chats` で一覧を取得し、読むチャットを名前で選びます。選んだ一覧の `chat_ref` を使って、通常LINEは `personal_messages`、OpenChatは `openchat_messages` を呼び出します。

たとえば「通常LINEのチャット一覧を表示して」「OpenChatの一覧から○○を選んで、取得できるメッセージを表示して」と依頼できます。通常LINE・OpenChatで別々のQRログインは不要です。認証が期限切れになる場合は本人が再認証します。

## 定期監視と別プロファイル

監視は初期状態で無効です。取得したメッセージをローカルの暗号化アーカイブに保存します。設定は docs/USER-GUIDE.md の定期監視節を参照し、Linuxでは scripts/linebridge.ps1 を bash scripts/linebridge.sh に読み替えてください。

別プロファイルが必要なら、実行するすべてのターミナルで同じ保存先を指定します。既存プロファイルへのインストールは上書きせず停止します。

```bash
export LINEBRIDGE_DATA_DIR="$HOME/.local/share/linebridge/profiles/friend-trial"
bash ./LineBridge.sh
```

## 保存と制限

標準保存先は `~/.local/share/linebridge/profiles/default` です。`XDG_DATA_HOME` を指定している場合はその下の `linebridge/profiles/default` を使います。

保存先は本人所有の0700、ファイルは0600です。認証と本文はAES-256-GCMで暗号化し、32バイトの鍵は同じ保存先の `profile-key.bin` に0600で保存します。WindowsのDPAPIとは異なり、Linuxユーザーのファイル権限を保護の前提にします。同じユーザーで動くプログラムやroot、ディスクを読める人から守るものではありません。ディスク全体の保護にはUbuntu側のディスク暗号化を使ってください。暗号化ファイルの拡張子 `.dpapi` は既存コードとの互換用の名前で、LinuxではDPAPIを使いません。

権限が広い保存先・鍵、シンボリックリンク、ハードリンクを検出したら停止します。鍵が失われても自動で作り直しません。Windowsから認証情報をコピーして使う方式は提供しません。友人は自分のアカウントで新規認証してください。

配布物に認証・チャット本文・履歴・ローカル設定・依存物バイナリは含みません。展開後のフォルダや保存先全体を送り直さず、配布アーカイブだけを渡してください。

非公式連携のため、LINE側の変更やアカウント制限、既存PC用ログインへの影響があり得ます。AIに取得させた本文は、そのAIサービスへ渡ります。取得する部屋は本人が選び、参加者の個人情報の扱いに注意してください。

個人・参加済みグループ・OpenChatのテキスト送信は既定で有効です。設定がない古いプロファイルも有効として扱います。停止中の`sending disable` / `sending enable`で切り替えられ、送信ごとの確認承認・宛先の再確認・送信間隔の制限はありません。監視間隔、永続保存の検索は`docs/USER-GUIDE.md`を参照してください。実配信は本人の端末で確認してください。OpenChatの全履歴や最新N件は保証しません。写真は明示取得に対応し、動画・音声・ファイルの取得は提供しません。

友人の実アカウントでのQR認証と読取は、本人のPCでの確認が必要です。確認項目は `docs/LINUX-TRIAL-CHECKS.md` を参照してください。失敗時は `8 Doctor` の状態とエラーコードだけを開発者へ伝え、QR・PIN・鍵・本文・保存先を添付しないでください。
