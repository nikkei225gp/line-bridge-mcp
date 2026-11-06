# 依存物と配布前の確認事項

本体のソースは`LICENSE`にあるMIT Licenseです。GitHubとソース配布物に、認証情報・保存メッセージ・ダウンロードした実行環境は含めません。

| 依存物 | 固定方法 | 表記 |
| --- | --- | --- |
| Python依存物 | `pyproject.toml`と`uv.lock`の版・ハッシュ | パッケージごとの表記を確認 |
| linejs | `ef6c3d9f70dd41fa51053615d47f071f58cf8db3`とSHA-256 | MIT、`third_party/linejs-MIT.txt` |
| Deno 2.9.7 | `runtime-pins.json`のURLとSHA-256 | MITおよび同梱依存の通知 |
| Linux用uv 0.12.17 | `runtime-pins.json`のURLとSHA-256 | MIT / Apache-2.0ほか |
| 任意のSecure MCP Tunnel | 自動取得・同梱しない | ベンダーのLICENSE・NOTICE・SBOMを確認 |

0.2.0では旧方式のokline / line-personal-mcp依存とNode実行環境の導入を削除しました。通常LINEとOpenChatは共通認証のlinejs APIを使います。Denoで使うnpm依存は`src/line_bridge/js/deno.lock`で固定します。

`docs/DEPENDENCIES.json`と`docs/LICENSE-ASSETS.json`はローカルの依存情報とライセンス資源の確認記録です。宣言やファイルの存在だけでは、組み込みWASM・生成型・音声関連の推移依存を再配布できると確認したことにはなりません。Deno、linejs、任意のTunnelの完全な由来・配布条件の確認は残っています。

以前の検査で見つかったThriftの脆弱性に対応して0.23.0に固定しています。旧版の脆弱性確認記録は、その当時の対象と情報に限られます。固定版であることや既知の情報がないことだけで安全とは判断しません。

ソースとGit履歴は秘密パターン検査とGitleaksでPC内で確認します。プロファイルや実行環境を検査用コピーへ含めません。検査結果は秘密がないことの保証でも、人による独立確認でもありません。

実行環境をバイナリ入りで再配布する場合は、ソース管理とは別に条件を確認します。署名付きバイナリ版、新しいWindowsユーザーや別PCでの認証、0.2.0のLinux実送信は未確認です。
