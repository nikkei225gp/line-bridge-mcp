# 利用手順

この手順は0.3.0の共通認証方式向けです。旧版の個別ログインと`legacy-readonly`は提供しません。

## 保存先と起動

新規導入では`default`プロファイルを使います。別プロファイルでは自分の保存先へ変えます。

```powershell
$DataDir = Join-Path $env:LOCALAPPDATA 'LineBridge\profiles\default'
.\scripts\linebridge.ps1 --data-dir $DataDir start
.\scripts\linebridge.ps1 --data-dir $DataDir status
.\scripts\linebridge.ps1 --data-dir $DataDir doctor
```

新規導入だけ`scripts/install.ps1`を使います。Linuxでは`LineBridge.sh`を使います。Python、Deno、linejsを固定版で導入します。Node実行環境と旧方式のPython依存は導入しません。

CLI は停止中に `login --device android --output json`、AI からは稼働中の `login_start` / `login_status` で認証します。QR は Base64 PNG と URL、必要時の PIN はテキストで表示できます。本人がメイン端末で操作します。再認証は明示的な `--replace` または `replace=true` が必要です。監視は再認証後に無効へ戻り、送信の設定は維持します。通常の起動は保存認証を再利用し、自動再認証はしません。

## ログイン端末

| CLI の指定 | MCP の device | 利用枠 |
| --- | --- | --- |
| `android`（新規既定） | `ANDROIDSECONDARY` | Android サブ端末 |
| `ipad` | `IOSIPAD` | iPad サブ端末 |
| `windows` | `DESKTOPWIN` | PC |
| `mac` | `DESKTOPMAC` | PC |

公式のサブ端末枠は Android・PC・iPad 各1台です。Windows と Mac は同じ PC 枠です。同種端末の追加ログインは既存セッションへ影響し得ます。メイン端末の引き継ぎ・新規登録は行いません。

## MCPへの登録

`clients --format mcp` は汎用 JSON を表示します。生成コマンドの `--auto-start` により、MCP ホスト接続時にブリッジを起動できます。`clients --format opencode`はOpenCode V2の`mcp.servers.line`設定を、`clients --format codex`はCodexのTOMLを表示します。既存の他の設定は残してください。各クライアントの`--data-dir`を同じ保存先にすると、1つのデーモンを共有します。

更新後はクライアント側でツール一覧を更新します。新しい会話で`bridge_status`を呼び、バージョンを確認します。古いツール名の互換対応はありません。

## メッセージの取得と保存

通常LINEは`personal_chats`と`personal_messages`、参加済みグループは`personal_groups`と`personal_messages`を使います。連絡先を探す場合は`personal_find_contact` → `personal_contact_chats`です。OpenChatは`openchat_chats`と`openchat_messages`を使います。返された不透明な`chat_ref`をそのまま渡し、生IDを作りません。

取得したメッセージは本文を暗号化してSQLiteに保存します。`archive_messages`はLINEに接続せず、個人・グループ・OpenChatの保存データを読みます。

投稿された写真（`kind`が`image`）は、`message_image`に`message_ref`を渡すと画像として返します。対象は一度取得して保存したメッセージだけです。`preview: true`ではLINEの縮小画像を返します。上限は6MiBで、画像は保存しません。動画・音声・ファイルは安全のため取得しません。保存期限を過ぎた写真は`image_unavailable`になります。

チャットの参加者は`chat_members`に`chat_ref`を渡すと、表示名で確認できます（通常LINEのグループ・ルーム・1対1、参加済みのOpenChat）。IDは返しません。通常LINEは自分とグループ作成者の印、OpenChatは各メンバーの役割（admin・co_admin・member）が付きます。`limit`は最大200件（初期値100件）で、通常LINEは`total`に全人数、`truncated`に打ち切りの有無が入ります。

引用リプライは、`send`に`reply_to_message_ref`（同じチャットの、一度取得して保存したメッセージ）を加えます。結果の`reply_confirmed`が`true`ならLINEが引用を受け付けた応答を確認できた状態、`false`なら本文は送信済みでも引用になったかは確認できていません（その場合も再送しません）。スレッドへの投稿（`thread_message_ref`）とは同時に使えません。

OpenChatのスレッドは、`openchat_messages`の結果で`thread.reply_count`が付いたメッセージが元になります。その`message_ref`を`openchat_thread_messages`に渡すと返信を読めます。返信は`thread_root_ref`付きで保存され、`archive_messages`でも探せます。スレッドへの投稿は`send`に`thread_message_ref`（同じチャットのメッセージ）を加えます。スレッドが無いメッセージに送ると、新しいスレッドができます。スレッドの返信は新着監視と通知の対象外です。

自分の送信は`unsend`に`message_ref`を渡すと取り消せます（通常LINE・OpenChat、スレッドの返信も可）。LINEの制限で送信から約24時間以内だけです。OpenChatの副管理者以上は`openchat_delete_message`で他人の投稿も削除できます。一般メンバーはLINEに拒否されます。対象は一度取得して保存したメッセージだけで、結果は`unsent`/`deleted`か、`not_unsent`/`not_deleted`と理由です。

| 引数 | 用途 |
| --- | --- |
| `chat_ref` | 指定チャットに限定 |
| `query` | 本文の部分一致、大文字小文字を区別しない |
| `message_ref` | 保存済みの1件を正確に取得 |
| `after_ms`・`before_ms` | Unix時刻ミリ秒で期間を指定 |
| `limit` | 最大100件、初期値50件 |
| `before_message_ref` | 前の結果の`next_before_message_ref`を渡して続きへ進む |

1回の検索で復号するのは最大500件です。該当がなくても`truncated=true`なら続きがあります。保存範囲だけの検索であり、取得できなかった過去データを含む全履歴ではありません。保存データは監視を止めても残り、自動では削除しません。

## 定期監視

先にチャット一覧を取得し、停止中に設定します。

```powershell
.\scripts\linebridge.ps1 --data-dir $DataDir stop
.\scripts\linebridge.ps1 --data-dir $DataDir monitor enable --all --include-future-chats --interval-seconds 120
.\scripts\linebridge.ps1 --data-dir $DataDir start
```

`--chat-ref '<参照>'`を複数指定すれば対象を限定できます。`--all`は一覧取得済みのチャット、`--include-future-chats`は今後一覧に現れるチャットも対象にします。通常LINEの一覧には上限があるため、全チャットを漏れなく監視する保証はありません。通常LINEは最近のトーク一覧で最終メッセージが変わっていないチャットを読み直しません。

間隔は1〜86400秒で指定できます。短い間隔ではAPI負荷が増えるため、通常は120秒以上を使います。処理が間隔を超えた場合は重ねて実行しません。通信失敗時は次の周期で再確認します。監視は取得したメッセージをローカルの暗号化アーカイブに保存します。外部への通知は次節の`notify`を有効にした場合だけ行います。

初回の履歴、自分の送信、時刻が不自然なメッセージも、取得できた内容をローカルに保存します。停止するときは`stop` → `monitor disable` → `start`です。

## 新着通知（Grok Botなど）

監視で見つけた新着を、エージェントのwebhook（例: Grok Botのルーティンのwebhook URLとキー）へまとめて送ります。エージェントに定期取得させる必要はなく、新着1件ごとに起こすこともしません。通知には件数・チャット・数件の抜粋だけを入れるので、本文全体はエージェントが`archive_messages`で取得します。

```powershell
.\scripts\linebridge.ps1 --data-dir $DataDir stop
$env:LINEBRIDGE_NOTIFY_KEY = '<webhookのキー>'
.\scripts\linebridge.ps1 --data-dir $DataDir notify enable --url 'https://<webhook URL>'
.\scripts\linebridge.ps1 --data-dir $DataDir notify test
.\scripts\linebridge.ps1 --data-dir $DataDir start
```

キーは環境変数`LINEBRIDGE_NOTIFY_KEY`か`--key-stdin`（標準入力）で渡し、コマンド引数には書きません。URLとキーはプロファイル内に暗号化して保存します。先に`monitor enable`が必要です。

| 区分 | 対象（初期値） | 送るタイミング |
| --- | --- | --- |
| 緊急 | 自分へのメンション・@all、自分の発言への返信、`--keyword`に一致 | 次の周期ですぐ（通知しない時間帯でも送る） |
| 即時 | 1対1のトーク、`--instant-chat`で指定したチャット | 最初の新着から`--debounce-seconds`（120秒）待ってまとめて送る |
| まとめ | 上記以外のグループ・OpenChat | `--digest-minutes`（60分）ごと。新着がなければ送らない |
| 通知しない | `--mute-chat`で指定したチャット | 送らない |

- 送るときは、その時点で保留中の新着をすべて1回にまとめます。
- `--quiet-hours 0-7`（初期値）の間は緊急以外を保留します。時刻は`--utc-offset-minutes`（初期値540＝日本時間）で決めます。同じ値を2つ（例: `0-0`）にすると無効です。
- `--hourly-cap`（初期値6）を超える分は次の通知にまとめます。
- 1対1も「まとめ」にする場合は`--no-direct-instant`を付けます。
- 送信に失敗した新着は保留したまま、次の周期で再送します。
- 実際の通知までの遅れは、監視間隔＋待ち時間です。即時性を上げる場合は監視間隔を60秒程度にします。
- 状態は`bridge_status`の`notify`で確認できます。停止するときは`stop` → `notify disable` → `start`です。

通知本文の`hint`のとおり、メッセージ本文は信頼できない入力として扱ってください。

## テキスト送信

送信は既定有効で、設定がない古いプロファイルも有効として扱います。停止中の `sending disable` / `sending enable` で切り替えられ、追加の承認フラグは不要です。

`send`に`chat_ref`、本文、UUIDの`request_id`を渡すと、その場で1回送信します。確認承認はありません。同じ`request_id`を再送しても重複送信しません。

個人・参加済みグループ・OpenChatを同じツールで送信します。宛先の再確認・本文の承認・送信間隔の制限はありません。通常LINEはE2EEで送り、必要な鍵がない場合は送信しません。鍵の新規登録や平文への切替は行いません。

`send_outcome_unknown_no_retry`では再送しません。UUIDを変えて送ることもせず、LINE画面を本人が確認します。送信前にUUIDと連番をDBへ確定するため、異常終了後も同じ予約で再送しません。

## 更新・削除

更新前にデーモンを停止し、保存先を安全にバックアップします。Git のソースを更新後、Windows では `runtime/uv/uv.exe sync --frozen --no-dev --no-editable --python 3.11.15`、Linux では `runtime/uv/uv sync --frozen --no-dev --no-editable --python 3.11.15` を実行してから起動します。認証やDBをGitHubへ含めません。既存の起動タスクと MCP 登録では、同じ保存先を維持してください。

削除する場合は対象の起動タスクと`line`登録だけを外し、停止後にアプリとプロファイルを削除します。プロファイルを削除すると認証・メッセージ・送信記録も失われます。一括削除コマンドはありません。
