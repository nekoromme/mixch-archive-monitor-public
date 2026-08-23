# MixChannel Archive Monitor

MixChannelの公開アーカイブ更新を定期確認し、更新があった監視対象をDiscordへ通知する個人用ツールです。

## 公開範囲

このリポジトリには、監視プログラムとGitHub Actionsの設定だけを置いています。
次のデータは公開せず、別の非公開リポジトリから実行時だけ取得します。

- 監視対象のIDと表示名
- 最終確認状態
- アーカイブ活動日
- Discord Webhook、ログイン情報、非公開リポジトリ用トークン

公開Actionsのログと成果物では、監視対象のID・名前・URL・個別エラー内容を除外します。

## 一時運用

GitHub Actionsの実行場所を一時的に公開リポジトリへ移すための構成です。
定期監視は日本時間2026年9月1日以降、自動的に実処理を停止します。
手動実行は動作確認用として期限後も利用できます。

## 必要なRepository secrets

- `PRIVATE_DATA_SSH_KEY`: 同じ所有者の非公開`mixch`リポジトリだけを読み書きできる一時Deploy key
- `DISCORD_WEBHOOK_URL`: 更新通知を送信するDiscord Webhook

監視結果と状態ファイルは従来どおり非公開リポジトリへ保存します。秘密値は公開リポジトリのファイルやログには保存しません。

トークンは対象リポジトリとContents権限だけに絞り、期限を設定してください。

## ローカルテスト

```bash
python -m unittest discover -s tests -v
```
