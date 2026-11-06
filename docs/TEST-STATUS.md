# 検証

公開版の自動テストは合成データと隔離した一時プロファイルを使用します。LINE の実ログイン・読取・投稿は実行しません。

## 公開準備時の検証（2026-11-06）

| 対象 | 結果 |
| --- | --- |
| Windows の Python テスト | 222件中212件成功、Linux専用10件スキップ |
| 固定 Deno/LINEJS の TypeScript テスト | 132件成功 |
| 合成データの MCP stdio | 2接続・QR画像・非同期ログイン・送信UUIDの重複抑止を確認 |
| ソースの秘密情報検査 | 配布許可リスト検査と Gitleaks 8.30.1 で指摘なし |

実アカウントへの接続・投稿はこの検証に含めません。

## ローカルでの実行

導入済み環境で次を実行します。

```powershell
.\.venv\Scripts\python.exe -B tools/run-tests.py
.\.venv\Scripts\python.exe -B tools/smoke-agent.py
.\.venv\Scripts\python.exe -B tools/release-scan.py
```

Linux では Python のパスを `.venv/bin/python` に読み替えます。固定 Deno/LINEJS のテスト実行例は `.github/workflows/tests.yml` にあります。

`tools/run-tests.py` は外部通信・DNS・子プロセスを禁止し、保存先を一時ディレクトリへ隔離します。`tools/smoke-agent.py` はローカル stdio と合成 QR/LINE データで検証します。

## 公開前の検査

`tools/release-scan.py` は配布許可リストのソースと全ローカル参照の Git 履歴を検査します。GitHub Actions では別途 Gitleaks で秘密情報を検査します。検出なしは完全な不在保証ではありません。

新しい PC での認証、実配信、通信断や認証切れからの復旧は自動テストと区別します。[実アカウント確認](LIVE-CHECKS.md) を参照してください。
