# ランキング監視（アーカイブ管理画面に統合）

管理画面の折りたたみ「監視設定」で、アーカイブとランキングを別々にオン・オフできます。
管理画面：https://mixch-archive-admin.purplepearl-v.workers.dev/

- アーカイブ：ルートの `mixcha_watcher.py` と従来の実行時刻を使用。
- ランキング：このフォルダの監視処理をGitHub内の5分待機から起動。追加の鍵や画面設定は不要。
- オフでは取得・通知・自動復旧をしません。実行中の1回だけは完了します。
- 5分待機は1本だけ実行し、待機中は1分ごとに停止操作を確認。GitHubの標準実行環境を使用。
- 起動済みのランキングが終わっていない場合は追加起動しません。GitHubの混雑による待ち時間はあり得ます。
- 予期せぬ停止は `ranking-watchdog.yml` が再起動を試みます。再開直後12分間は最初の実行を待ち、故障通知を抑制します。
- 旧リポジトリ側の全監視ツールの故障確認も、このオン・オフを尊重します。

## 保存場所

| 内容 | 保存先 |
| --- | --- |
| 全体設定 | main の `monitor_settings.json` |
| ランキング通知済み履歴・夜間候補 | ranking-state ブランチの `state.json` |
| 自動復旧の通知履歴 | ranking-watchdog-state ブランチの `state.json` |
| 定期起動・停止確認 | `ranking-relay.yml` の実行ログ |

`enabled` はアーカイブ、`ranking_enabled` はランキングの利用者設定です。
`ranking_ready` は移行完了を示す内部設定で、利用者用スイッチからは変更しません。
履歴を取得できない場合は、空の履歴で始めずエラーで止めて大量再通知を防ぎます。
コード更新時の自動テスト実行は、実際の通知と履歴保存を行いません。

## 引き継いだ通知条件

- 昼間は勢い度150を超えた配信（151以上）。同じ配信者の再通知は12時間抑制。
- 夜間は元の処理を維持：22:00〜翌06:59は150以上の候補を蓄積し、朝に公開アーカイブのある配信者をまとめて通知。
- 初期ブロックリスト、代替取得先、取得失敗時の再試行を維持。
- Discordの接続先は統合先の既存Secret `DISCORD_WEBHOOK_URL` を使用。
- Cloudflareの既存Secret `GITHUB_TOKEN` は一覧・監視設定の編集に使用。監視の起動はGitHub内の実行ごとの鍵で行い、Cloudflare側の鍵への追加権限は不要です。

## 動作確認

このフォルダで `python3 -m unittest discover -s tests -v`。
管理画面側は `cloudflare-admin` で `npm test`。
GitHub Actionsの「MixChannelランキング監視」は、手動実行時の `dry_run` をオンにすると通知せずに確認できます。

公開リポジトリの標準実行環境は無料です。非公開化した場合は課金体系が変わります。
公式資料：https://docs.github.com/en/billing/concepts/product-billing/github-actions

Cloudflareの定期起動は使用せず、設定にも残しません。再開時は初回の監視と次回の5分待機をGitHub側で予約します。
