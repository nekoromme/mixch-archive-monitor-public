# MixChannel Archive Monitor

MixChannelの公開アーカイブ更新を定期確認し、更新があった監視対象をDiscordへ通知する個人用ツールです。

## 公開リポジトリ単体で動作

監視プログラム・監視対象・監視状態をこのリポジトリだけで管理します。旧非公開リポジトリや秘密鍵への依存はありません。

- `watchlist.json`: 監視対象のIDと表示名
- `state.json`: 対象ごとの最終確認状態
- `activity_state.json`: アーカイブ活動日と通知状態

これら3ファイルは平文で公開されます。Discord WebhookはGitHub ActionsのSecretにだけ保存し、ファイルやログへ出しません。

## スマホから監視対象を編集

次のURLをスマホのホーム画面へ登録します。

https://github.com/nekoromme/mixch-archive-monitor-public/edit/main/watchlist.json

IDだけ追加して名前を自動取得させる場合は、名前を `__AUTO_NAME__:ユーザーID` の形式で保存します。`Resolve MixChannel Profile Names` が起動し、MixChannelプロフィール名の先頭10文字へ置き換えます。

例:

```json
{
  "id": "18999999",
  "name": "__AUTO_NAME__:18999999"
}
```

## 定期実行

GitHub Actionsで日本時間の06:07、17:07、19:07、21:07に実行します。土日は08:07と14:07も追加します。期限による自動停止はありません。

監視結果は同じリポジトリの状態ファイルへ書き戻します。スマホ編集と定期監視が重なった場合も、同じ同時実行制御を使って順番に処理します。

## 必要なRepository secret

- `DISCORD_WEBHOOK_URL`: 更新通知を送信するDiscord Webhook

旧構成で使っていた `PRIVATE_DATA_SSH_KEY` と非公開リポジトリ用トークンは不要です。

## ローカルテスト

```bash
python -m unittest discover -s tests -v
```
