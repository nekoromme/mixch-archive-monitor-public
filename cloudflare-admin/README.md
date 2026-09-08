# ミクチャ管理画面：Cloudflare移行版

各対象の追加・編集・削除・アーカイブ監視のオンオフに対応。
GitHubの同じ `watchlist.json` を読み書きするため、対象一覧の引っ越しは不要です。
名前の自動取得とアーカイブ監視は、既存のGitHub側の処理が続けて担当します。

**移行用コードは準備済みです。Cloudflareとの初回接続・公開はまだ完了していません。**
以下を一度設定すると、今後はこのフォルダのコードを更新するだけで自動公開できます。

## 1. GitHubを読み込んで公開する

1. [Cloudflareの管理画面](https://dash.cloudflare.com/)へログインします。
2. 「Workers & Pages」→「Create application」（アプリを作成）へ進みます。
3. GitHubから読み込む項目「Import a repository」または「Continue with GitHub」を選択します。
4. GitHubの連携許可画面が出たら、次のリポジトリを許可します。
   **nekoromme / mixch-archive-monitor-public**
5. 以下の設定で作成・公開します。Pagesではなく**Worker**を作ります。

| 画面の項目 | 入れる内容 |
| --- | --- |
| Worker name / Project name（名前） | `mixch-archive-admin` |
| Repository（リポジトリ） | `nekoromme/mixch-archive-monitor-public` |
| Production branch（本番ブランチ） | `main` |
| Root directory（ルートディレクトリ） | `cloudflare-admin` |
| Build command（ビルドコマンド） | `npm run build` |
| Deploy command（公開コマンド） | `npm run deploy` |

依存関係はCloudflare側で自動インストールされます。
インストール用の欄が表示された場合は `npm ci` を指定します。
プレビュー用ブランチの公開は無効のままで構いません。
**この段階で画面が403になっても正常です。次のログイン保護の設定が必要です。**

## 2. 自分だけが開けるようにする

1. 作成した `mixch-archive-admin` を開きます。
2. 「Access」タブ→「Protect this Worker behind Access」を押します。
3. 保護対象は **All traffic（すべてのアクセス）** を選びます。
4. 「Authentication policy」は **Cloudflare account** を選びます。
   これはCloudflareアカウントのメンバーだけを許可する設定です。
   個人アカウントにほかのメンバーがいる場合は、Zero TrustのAccess設定で
   許可するメールアドレスを自分だけに絞ります。
5. 「Apply Access」で保存します。

Zero Trustの初期登録を求められた場合は、無料プランで初期設定した後、
このWorkerのAccess画面へ戻ってください。
「Email domain」に `gmail.com` などを指定すると範囲が広すぎるため、
自分だけの利用には使いません。

このアプリはCloudflareが認証した `ctx.access` を確認します。
保護を設定し忘れた場合や、偽の認証ヘッダーが届いた場合は、
管理画面も保存処理も開きません。

## 3. GitHubを書き換える鍵を一度だけ登録する

新しい鍵の発行は必須ではありません。これまで動いていた管理画面の鍵を移せます。

1. [これまでのGoogle Apps Script](https://script.google.com/d/1NDx_r9jlN--6Rymw4R5rdGKrsaJGNezl7KlhB--jYwfA_z3ZPsh0PJVn/edit)を開きます。
2. 左側の歯車「プロジェクトの設定」→「スクリプト プロパティ」へ進みます。
3. **GITHUB_TOKEN** の値をコピーします。チャットや公開コードには貼りません。
4. CloudflareのWorker→「Settings」→「Variables and Secrets」→「Add」。
5. 種類を **Secret**、名前を **GITHUB_TOKEN** とし、値へコピーした鍵を貼り付けます。
6. 「Deploy」など、変更を公開するボタンまで押します。

登録先は実行用の「Variables and Secrets」です。
ビルド用の変数やGitHubのソースファイルへは入れません。
既存の鍵が期限切れの場合は、このリポジトリだけを対象に
ContentsのRead and write（読み書き）権限を付けた鍵へ交換します。

## 4. 無関係な更新で再公開しないようにする

このリポジトリには監視結果も定期保存されます。
それまで管理画面の再公開対象に含めると無駄なビルドが増えるので、初回に絞ります。

Worker→「Settings」→「Build」または「Builds」→「Build watch paths」。

| 項目 | 設定 |
| --- | --- |
| Include paths（含めるパス） | `cloudflare-admin/*` |
| Exclude paths（除外するパス） | 空欄 |

含めるパスに最初からある `*` は、この設定に置き換えます。
パスはリポジトリの一番上から指定します。

## 5. 最初の動作確認

1. Workerの「Visit」から新しいURLを開き、本人のアカウントでログインします。
2. いつもの対象一覧が表示されることを確認します。
3. 対象を1件「編集」し、アーカイブ監視をオフにして保存します。
4. ページを開き直し、同じ対象が「オフ（停止中）」のままであることを確認します。
5. 一時停止が目的でない場合はオンに戻して保存します。
6. 新しいURLをスマホのブックマークへ登録します。

新しいURLをチャットへ送れば、こちらでも接続・反映状況の確認を続けられます。
公開後の実際のログイン・保存は、初回接続が終わるまでは未検証です。
旧管理画面は動作確認が終わるまで残して構いません。
その後は新画面を使い、両方から同時編集しないでください。

## 今後の修正

「ボタンを増やして」「並べ替えたい」などと依頼するだけで、
このフォルダを修正→テスト→GitHubへ反映→Cloudflareが自動公開します。
Google Apps Scriptへのコピペと手動のバージョン更新は不要です。
アカウント連携の解除や鍵の期限切れが起きたときは、認証のやり直しが必要です。

## 不具合が起きたら

- 403：Access保護が未設定・認証切れ・本人が許可対象に含まれていない可能性。
- 一覧に「初期設定が未完了」：実行用SecretのGITHUB_TOKENを確認。
- GitHub接続のエラー：鍵の期限、対象リポジトリ、Contentsの読み書き権限を確認。
- 「一覧が更新されました」：同時編集や名前自動取得との競合。戻って再読み込み。
- 通信結果が不明：保存を連打せず、再読み込みして現在の状態を確認。

エラー画面の「確認番号」を送れば、Workerのログと照合できます。
通常ログは確認番号・処理時間・結果だけで、鍵や配信者名は出力しません。

## 開発・検証用

`npm ci` → `npm run build` で通信テストと公開用の変換を確認できます。
`npm run deploy` はCloudflareへ実際に公開するため、認証済み環境だけで使用します。
テストは本物のGitHubへ接続せず、監視対象や通知先を変更しません。

HTMLはWorkerへ文字列として同梱しています。
**Static Assetsを追加すると内部ルーターがctx.accessを渡さないため、この認証方式では使いません。**

公式資料：
- [GitHubからの自動公開](https://developers.cloudflare.com/workers/ci-cd/builds/)
- [Worker単位のログイン保護とctx.access](https://developers.cloudflare.com/workers/configuration/cloudflare-access/)
- [変更を監視するパスの設定](https://developers.cloudflare.com/workers/ci-cd/builds/build-watch-paths/)
