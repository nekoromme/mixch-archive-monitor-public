/**
 * Google Apps Script Web admin for editing the repository watchlist JSON.
 *
 * Required Script Property:
 * - GITHUB_TOKEN
 *
 * The repository destination is fixed below so that old Script Properties do
 * not silently send the smartphone app to a deleted or renamed repository.
 */

const SCRIPT_PROP_KEYS = ['GITHUB_TOKEN'];

/*
 * 現在実際に稼働している公開版の保存先です。
 *
 * 以前使っていた非公開リポジトリや旧ユーザー名を
 * スクリプトプロパティに残したままでも、この値を優先します。
 * これにより、スマホ画面だけが削除済みリポジトリを見続ける事故を防ぎます。
 */
const GITHUB_TARGET = Object.freeze({
  owner: 'nekoromme',
  repo: 'mixch-archive-monitor-public',
  branch: 'main',
  jsonPath: 'watchlist.json',
});

/*
 * 画面や実行ログから、新しい版へ切り替わったか確認するための識別子です。
 * トークンなどの秘密情報は含めません。
 */
const APP_VERSION = '2026-09-08-archive-toggle';

/*
 * Google Apps Scriptからミクチャへ直接アクセスすると
 * HTTP 403で拒否されるため、いったん取得待ちの印を保存します。
 *
 * watchlist.jsonの更新を検知したGitHub Actionsが、
 * Chromeでプロフィールを開いて実際の名前へ置き換えます。
 */
const PENDING_NAME_PREFIX = '__AUTO_NAME__:';

function doGet() {
  return HtmlService.createHtmlOutputFromFile('Index')
    .setTitle('MixChannel 監視対象管理')
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

function getWatchlist() {
  const file = fetchJsonFile_();
  const watchlist = normalizeWatchlist_(file.json);
  return {
    items: watchlist.items,
    meta: {
      count: watchlist.items.length,
      repository: getConfig_().owner + '/' + getConfig_().repo,
      jsonPath: getConfig_().jsonPath,
      branch: getConfig_().branch,
      appVersion: APP_VERSION,
      duplicateIds: findDuplicateIds_(watchlist.items),
    },
  };
}

/*
 * Apps Scriptの編集画面からこの関数だけを実行すると、
 * 一覧を書き換えずに接続確認できます。
 * 成功時は件数と接続先だけを返し、トークンは絶対に表示しません。
 */
function checkConnection() {
  const file = fetchJsonFile_();
  const watchlist = normalizeWatchlist_(file.json);
  const config = getConfig_();

  return {
    ok: true,
    appVersion: APP_VERSION,
    repository: config.owner + '/' + config.repo,
    branch: config.branch,
    jsonPath: config.jsonPath,
    count: watchlist.items.length,
    sha: file.sha,
  };
}

function addStreamer(streamer) {
  const id = validateStreamerId_(streamer);
  const file = fetchJsonFile_();
  const watchlist = normalizeWatchlist_(file.json);

  if (watchlist.items.some((item) => item.id === id)) {
    throw userError_('同じIDの配信者が既に存在します: ' + id);
  }

  watchlist.raw.push({
    id: id,
    name: PENDING_NAME_PREFIX + id,
  });

  saveJsonFile_(watchlist.raw, file.sha, 'Add streamer ' + id);

  return {
    ok: true,
    message: '登録しました。名前は通常1～2分で自動取得されます。',
  };
}

function updateStreamer(target, streamer) {
  const input = validateStreamer_(streamer);
  const safeTarget = validateTarget_(target);
  const file = fetchJsonFile_();
  const watchlist = normalizeWatchlist_(file.json);
  const index = findTargetIndex_(watchlist.items, safeTarget);

  if (watchlist.items.some((item) => item.index !== index && item.id === input.id)) {
    throw userError_('同じIDの配信者が既に存在します: ' + input.id);
  }

  // 最新の設定と比較して、別の画面で変更されたオン・オフを上書きしません。
  if (typeof target.archiveEnabled === 'boolean' &&
      target.archiveEnabled !== watchlist.items[index].archiveEnabled) {
    throw userError_('監視設定が更新されています。再読み込みしてからやり直してください。');
  }
  // 名前だけの変更でも、監視設定や将来追加する項目を残します。
  watchlist.raw[index] = Object.assign({}, watchlist.raw[index], input);
  return saveJsonFile_(watchlist.raw, file.sha, 'Update streamer ' + input.id);
}

function deleteStreamer(target) {
  const safeTarget = validateTarget_(target);
  const file = fetchJsonFile_();
  const watchlist = normalizeWatchlist_(file.json);
  const index = findTargetIndex_(watchlist.items, safeTarget);
  const removed = watchlist.raw[index];

  watchlist.raw.splice(index, 1);
  return saveJsonFile_(watchlist.raw, file.sha, 'Delete streamer ' + String(removed.id));
}

function fetchJsonFile_() {
  const config = getConfig_();
  const url = githubContentsUrl_(config) + '?ref=' + encodeURIComponent(config.branch);
  const response = UrlFetchApp.fetch(url, {
    method: 'get',
    headers: githubHeaders_(config.token),
    muteHttpExceptions: true,
  });
  const result = parseGithubResponse_(response);
  if (!result.content || !result.sha) {
    throw userError_('GitHub APIのレスポンスにcontentまたはshaがありません。');
  }

  const text = Utilities.newBlob(Utilities.base64Decode(result.content.replace(/\s/g, ''))).getDataAsString('UTF-8');
  try {
    return { json: JSON.parse(text), sha: result.sha };
  } catch (error) {
    throw userError_('JSON parse error: GitHub上のJSON形式が壊れています。' + sanitizeMessage_(error.message));
  }
}

function saveJsonFile_(json, sha, message) {
  const config = getConfig_();
  const text = JSON.stringify(json, null, 2) + '\n';
  const payload = {
    message: message,
    content: Utilities.base64Encode(text, Utilities.Charset.UTF_8),
    sha: sha,
    branch: config.branch,
  };

  const response = UrlFetchApp.fetch(githubContentsUrl_(config), {
    method: 'put',
    headers: githubHeaders_(config.token),
    contentType: 'application/json',
    payload: JSON.stringify(payload),
    muteHttpExceptions: true,
  });
  parseGithubResponse_(response);
  return { ok: true, message: '保存しました' };
}

function normalizeWatchlist_(json) {
  if (!Array.isArray(json)) {
    throw userError_('この管理画面は現在のwatchlist.json形式（配列）専用です。JSON構造は変更しません。');
  }

  const items = json.map((item, index) => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) {
      throw userError_('JSON配列の' + (index + 1) + '件目がオブジェクトではありません。');
    }
    if (!Object.prototype.hasOwnProperty.call(item, 'id') || !Object.prototype.hasOwnProperty.call(item, 'name')) {
      throw userError_('JSON配列の' + (index + 1) + '件目にidまたはnameがありません。');
    }
    return {
      index: index,
      id: String(item.id),
      name: String(item.name),
      archiveEnabled: item.archive_enabled !== false,
      pendingName:
        String(item.name).indexOf(PENDING_NAME_PREFIX) === 0,
    };
  });

  return { raw: json, items: items };
}

function validateStreamerId_(streamer) {
  if (!streamer || typeof streamer !== 'object') {
    throw userError_('入力内容が不正です。');
  }

  const idInput = String(streamer.id || '').trim();

  if (!idInput) {
    throw userError_('IDは必須です。');
  }

  if (/^\d+$/.test(idInput)) {
    return idInput;
  }

  const urlMatch = idInput.match(
    /^https:\/\/mixch\.tv\/u\/(\d+)(?:\/(?:live|live_archives))?\/?(?:[?#].*)?$/
  );

  if (!urlMatch) {
    throw userError_(
      'IDは数字のみ、またはMixChannelのプロフィール・配信・アーカイブURLを入力してください。'
    );
  }

  return urlMatch[1];
}

function validateStreamer_(streamer) {
  const id = validateStreamerId_(streamer);
  const name = String(streamer.name || '').trim();

  if (!name) {
    throw userError_('名前は必須です。');
  }

  const result = { id: id, name: name };
  // 古い管理画面からの保存では設定を維持。新画面は真偽値で送信します。
  if (Object.prototype.hasOwnProperty.call(streamer, 'archive_enabled')) {
    if (typeof streamer.archive_enabled !== 'boolean') {
      throw userError_('監視設定が不正です。再読み込みしてください。');
    }
    result.archive_enabled = streamer.archive_enabled;
  }
  return result;
}

function validateTarget_(target) {
  if (!target || typeof target !== 'object') {
    throw userError_('編集・削除対象が不正です。');
  }
  const index = Number(target.index);
  const id = String(target.id || '');
  const name = String(target.name || '');
  if (!Number.isInteger(index) || index < 0 || !id || !name) {
    throw userError_('編集・削除対象が不正です。再読み込みしてからやり直してください。');
  }
  return { index: index, id: id, name: name };
}

function findTargetIndex_(items, target) {
  const item = items[target.index];
  if (!item || item.id !== target.id || item.name !== target.name) {
    throw userError_('GitHub側の内容が更新されています。再読み込みしてからやり直してください。');
  }
  return target.index;
}

function findDuplicateIds_(items) {
  const seen = {};
  const duplicates = {};
  items.forEach((item) => {
    if (seen[item.id]) {
      duplicates[item.id] = true;
    }
    seen[item.id] = true;
  });
  return Object.keys(duplicates);
}

function getConfig_() {
  const props = PropertiesService.getScriptProperties();
  const missing = SCRIPT_PROP_KEYS.filter((key) => !props.getProperty(key));
  if (missing.length > 0) {
    throw userError_('Script Propertiesが不足しています: ' + missing.join(', '));
  }
  return {
    token: props.getProperty('GITHUB_TOKEN'),
    owner: GITHUB_TARGET.owner,
    repo: GITHUB_TARGET.repo,
    branch: GITHUB_TARGET.branch,
    jsonPath: GITHUB_TARGET.jsonPath,
  };
}

function githubContentsUrl_(config) {
  return 'https://api.github.com/repos/' + encodeURIComponent(config.owner) + '/' +
    encodeURIComponent(config.repo) + '/contents/' + encodePath_(config.jsonPath);
}

function encodePath_(path) {
  return String(path).split('/').map(encodeURIComponent).join('/');
}

function githubHeaders_(token) {
  return {
    Authorization: 'Bearer ' + token,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
  };
}

function parseGithubResponse_(response) {
  const status = response.getResponseCode();
  let body = {};
  try {
    body = JSON.parse(response.getContentText() || '{}');
  } catch (error) {
    body = { message: response.getContentText() };
  }

  if (status >= 200 && status < 300) {
    return body;
  }

  if (status === 409) {
    throw userError_('GitHub側の内容が更新されています。再読み込みしてからやり直してください。');
  }
  if (status === 401 || status === 403) {
    throw userError_(
      status +
      ': GitHubトークンを確認してください。' +
      '「nekoromme/mixch-archive-monitor-public」のContents（読み書き）権限が必要です。'
    );
  }
  if (status === 404) {
    throw userError_(
      '404: 公開版リポジトリのwatchlist.jsonを取得できませんでした。' +
      'トークンの対象リポジトリ設定も確認してください。'
    );
  }

  throw userError_('GitHub API error ' + status + ': ' + sanitizeMessage_(body.message || '原因不明のエラー'));
}

function sanitizeMessage_(message) {
  const token = PropertiesService.getScriptProperties().getProperty('GITHUB_TOKEN');
  let safe = String(message || '');
  if (token) {
    safe = safe.split(token).join('[REDACTED]');
  }
  return safe;
}

function userError_(message) {
  return new Error(sanitizeMessage_(message));
}
