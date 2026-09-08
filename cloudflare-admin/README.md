# ミクチャ管理画面：Cloudflare移行版

各対象の追加・編集・削除・アーカイブ監視のオンオフに対応。
GitHubの同じ `watchlist.json` を読み書きするため、対象一覧の引っ越しは不要です。
名前の自動取得とアーカイブ監視は、既存のGitHub側の処理が続けて担当します。

**専用パスワード方式です。Zero Trustへの登録・支払い情報は不要です。**
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
**この段階ではADMIN_PASSWORDの初期設定エラーが出ても正常です。次のパスワード登録を行います。**

## 2. 専用パスワードを登録する（Zero Trust不要）

Zero Trustへの登録や支払い情報の入力は不要です。
既にWorkerを公開してGITHUB_TOKENを登録した場合、追加するのは次の1項目だけです。

1. Workerの「Settings」→「Variables and Secrets」→「Add variable」を開きます。
2. **Key** に `ADMIN_PASSWORD` を入力します。
3. **Value** に、この管理画面専用のパスワードを1～256文字で入力します。
   パスワード管理アプリ等で作った、推測されにくい英数字・記号の組み合わせがおすすめです。
4. **Secret** にチェックを入れます。
5. 「Add 1 variable」を押し、元の画面に「Deploy」「Save and deploy」があれば押します。
6. GitHub更新による公開処理が成功した後、Workerの「Visit」から開きます。
7. 表示されたログイン画面に同じパスワードを入力します。

パスワードはチャットやGitHubには貼らず、CloudflareのSecretへ直接登録します。
既に登録したGITHUB_TOKENはそのまま残します。

- ログイン状態は最長30日。ブラウザのCookie削除などで早く切れる場合があります。
- 管理画面の「ログアウト」でそのブラウザのログイン状態を消せます。
- ADMIN_PASSWORDを変更して公開すると、既存のログイン証明はすべて無効になります。
- パスワード未設定・空欄の場合は、管理画面も保存も開きません。
- ログインの連続試行はCloudflareの拠点ごとに約1分10回へ制限します。
  この制限は全世界共通の厳密な回数制限ではないため、十分に長いパスワードを使います。
- Zero Trustの初期登録画面は、そのまま戻って構いません。
  既にAccessでこのWorkerを保護した場合だけ、そのWorkerのAccess保護を解除します。
  アプリ自身の専用パスワード認証は引き続き有効です。

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

1. Workerの「Visit」から新しいURLを開き、専用パスワードでログインします。
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

- ログイン画面：専用パスワードを入力。
- ADMIN_PASSWORDの初期設定エラー：Secret名・文字数と公開処理の完了を確認。
- 429：ログインの試行回数制限。1分ほど待って再試行。
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
管理画面・保存処理は署名付きCookieで認証し、保存時は同一サイトからの送信かも確認します。

公式資料：
- [GitHubからの自動公開](https://developers.cloudflare.com/workers/ci-cd/builds/)
- [ログイン連続試行の制限](https://developers.cloudflare.com/workers/runtime-apis/bindings/rate-limit/)
- [変更を監視するパスの設定](https://developers.cloudflare.com/workers/ci-cd/builds/build-watch-paths/)
