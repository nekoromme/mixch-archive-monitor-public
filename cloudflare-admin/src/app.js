/**
 * ミクチャの管理画面。監視本体と同じwatchlist.jsonを読み書きします。
 * 個人の名前、認証情報、GitHubの返答本文はログへ出しません。
 */
const REPOSITORY = 'nekoromme/mixch-archive-monitor-public';
const CONTENTS_URL = 'https://api.github.com/repos/' + REPOSITORY + '/contents/watchlist.json';
const BRANCH = 'main';
const VERSION = '2026-09-08-cloudflare-1';
const AUTO_NAME = '__AUTO_NAME__:';
const CONFLICT = '一覧が別の画面や監視処理で更新されました。戻って「再読み込み」してからやり直してください。';

class UserError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

function json(value, status = 200) {
  return new Response(JSON.stringify(value), {
    status, headers: { 'Content-Type': 'application/json; charset=utf-8' },
  });
}

function secureResponse(response, requestId) {
  const copy = new Response(response.body, response);
  copy.headers.set('Cache-Control', 'no-store');
  copy.headers.set('X-Content-Type-Options', 'nosniff');
  copy.headers.set('X-Frame-Options', 'DENY');
  copy.headers.set('Referrer-Policy', 'no-referrer');
  copy.headers.set('X-Request-Id', requestId);
  // 既存画面のonclickとstyleを引き継ぐためinlineを許可。
  // 外部サイトへの通信や、他サイトからの埋め込みは許可しません。
  copy.headers.set('Content-Security-Policy',
    "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; " +
    "connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'; form-action 'self'");
  return copy;
}

// 日本語や絵文字を壊さないよう、UTF-8のバイト列をBase64へ変換します。
function encodeContent(text) {
  const bytes = new TextEncoder().encode(text);
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

function decodeContent(text) {
  return new TextDecoder('utf-8', { fatal: true }).decode(
    Uint8Array.from(atob(text.replace(/\s/g, '')), c => c.charCodeAt(0)));
}

function normalize(raw) {
  if (!Array.isArray(raw)) throw new UserError(502, '保存先の一覧形式が不正です。');
  return raw.map((entry, index) => {
    if (!entry || typeof entry !== 'object' || !/^\d+$/.test(String(entry.id)) ||
        typeof entry.name !== 'string') {
      throw new UserError(502, '保存先の一覧に不正な項目があります。');
    }
    return {
      index, id: String(entry.id), name: entry.name,
      archiveEnabled: entry.archive_enabled !== false,
      pendingName: entry.name.startsWith(AUTO_NAME),
    };
  });
}

function validateId(input) {
  if (typeof input !== 'string' || input.length > 500) {
    throw new UserError(400, '数字のIDかミクチャのURLを入力してください。');
  }
  const value = input.trim();
  const match = value.match(/^https:\/\/mixch\.tv\/u\/(\d+)(?:\/(?:live|live_archives))?\/?(?:[?#].*)?$/);
  const id = /^\d+$/.test(value) ? value : match?.[1];
  if (!id || id.length > 30) throw new UserError(400, '数字のIDかミクチャのURLを入力してください。');
  return id;
}

async function readBody(request) {
  if (request.headers.get('content-type')?.split(';')[0].trim() !== 'application/json') {
    throw new UserError(415, '送信形式が不正です。');
  }
  // 宣言された長さだけを信用せず、実際の受信量にも上限を付けます。
  const reader = request.body?.getReader();
  if (!reader) throw new UserError(400, '入力内容がありません。');
  const chunks = [];
  let size = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > 16384) {
      await reader.cancel();
      throw new UserError(413, '入力内容が長すぎます。');
    }
    chunks.push(value);
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  try {
    const body = JSON.parse(new TextDecoder().decode(bytes));
    if (!body || typeof body !== 'object' || Array.isArray(body)) throw new Error();
    return body;
  } catch { throw new UserError(400, '入力内容を読み取れませんでした。'); }
}

async function github(env, fetcher, method, body) {
  if (!env.GITHUB_TOKEN) throw new UserError(503, '初期設定が未完了です。管理者側の接続設定が必要です。');
  let response;
  try {
    response = await fetcher(CONTENTS_URL + (method === 'GET' ? '?ref=' + BRANCH : ''), {
      method,
      headers: {
        Authorization: 'Bearer ' + env.GITHUB_TOKEN,
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'mixch-watchlist-admin',
        'Content-Type': 'application/json',
        'Cache-Control': 'no-cache',
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
      signal: AbortSignal.timeout(15000),
      redirect: 'error',
    });
  } catch {
    throw new UserError(502, method === 'GET'
      ? '保存先との通信に失敗しました。少し待って再読み込みしてください。'
      : '保存結果を確認できません。連続で保存せず、一度再読み込みして確認してください。');
  }
  if (response.status === 409 || response.status === 422) throw new UserError(409, CONFLICT);
  if ([401, 403, 404].includes(response.status)) {
    throw new UserError(502, 'GitHubとの接続を確認してください。認証の期限・権限・接続先が原因の可能性があります。');
  }
  if (!response.ok) throw new UserError(502, '保存先が応答できませんでした。再読み込みしてください。');
  try { return await response.json(); }
  catch { throw new UserError(502, '保存先の応答を読み取れませんでした。再読み込みして確認してください。'); }
}

async function getFile(env, fetcher) {
  const file = await github(env, fetcher, 'GET');
  if (typeof file.sha !== 'string' || typeof file.content !== 'string') {
    throw new UserError(502, '保存先の応答形式が不正です。');
  }
  let raw;
  try { raw = JSON.parse(decodeContent(file.content)); }
  catch { throw new UserError(502, '保存先の一覧を読み取れませんでした。'); }
  return { raw, items: normalize(raw), sha: file.sha };
}

async function api(request, env, fetcher) {
  const path = new URL(request.url).pathname;
  if (request.method === 'GET' && path === '/api/watchlist') {
    const file = await getFile(env, fetcher);
    const counts = new Map();
    file.items.forEach(item => counts.set(item.id, (counts.get(item.id) || 0) + 1));
    return json({
      items: file.items, version: file.sha,
      meta: { count: file.items.length, appVersion: VERSION,
        duplicateIds: [...counts].filter(([, count]) => count > 1).map(([id]) => id) },
    });
  }
  if (!['/api/add', '/api/update', '/api/delete'].includes(path)) {
    throw new UserError(404, '指定された機能はありません。');
  }
  if (request.method !== 'POST') throw new UserError(405, 'この操作には保存ボタンを使用してください。');
  // 他サイトから、ログイン中のブラウザを勝手に操作する送信を拒否します。
  if (request.headers.get('Origin') !== new URL(request.url).origin) {
    throw new UserError(403, '別のページからの保存要求は受け付けません。');
  }
  const body = await readBody(request);
  if (typeof body.version !== 'string' || !body.version) throw new UserError(409, CONFLICT);
  const file = await getFile(env, fetcher);
  if (body.version !== file.sha) throw new UserError(409, CONFLICT);
  if (path === '/api/add') {
    const id = validateId(body.streamer?.id);
    if (file.items.some(item => item.id === id)) throw new UserError(400, '同じIDは既に登録されています。');
    file.raw.push({ id, name: AUTO_NAME + id });
  } else {
    const target = body.target;
    const entry = Number.isInteger(target?.index) && target.index >= 0
      ? file.items[target.index] : null;
    if (!entry || entry.id !== target.id || entry.name !== target.name) {
      throw new UserError(409, CONFLICT);
    }
    if (path === '/api/delete') {
      file.raw.splice(target.index, 1);
    } else {
      const id = validateId(body.streamer?.id);
      const name = body.streamer?.name;
      const enabled = body.streamer?.archive_enabled;
      if (typeof name !== 'string' || !name.trim() || name.length > 200) {
        throw new UserError(400, '配信者名を1～200文字で入力してください。');
      }
      if (typeof enabled !== 'boolean') throw new UserError(400, '監視のオン・オフ設定が不正です。');
      if (file.items.some(item => item.index !== target.index && item.id === id)) {
        throw new UserError(400, '同じIDは既に登録されています。');
      }
      file.raw[target.index] = { ...file.raw[target.index], id, name: name.trim(), archive_enabled: enabled };
    }
  }
  // shaの一致をGitHub側でも検証し、監視処理との同時更新による上書きを防ぎます。
  await github(env, fetcher, 'PUT', {
    message: 'Update watchlist from Cloudflare admin',
    content: encodeContent(JSON.stringify(file.raw, null, 2) + '\n'),
    sha: file.sha, branch: BRANCH,
  });
  return json({ ok: true, message: path === '/api/add'
    ? '登録しました。名前は通常1～2分で自動取得されます。' : '保存しました' });
}

// fetcherだけ差し替え可能にし、テストから本物のGitHubへ書き込まない構造です。
export function createApp(html, fetcher = (...args) => fetch(...args)) {
  return {
    async fetch(request, env, ctx) {
      const requestId = crypto.randomUUID();
      const start = Date.now();
      let response;
      try {
        // HTTPヘッダーのメールアドレスは信用しません。
        // Cloudflare自身が検証した実行コンテキストだけを使用します。
        if (!ctx?.access) throw new UserError(403, 'ログインが必要です。Cloudflare Accessの設定を確認してください。');
        const identity = await ctx.access.getIdentity();
        if (!identity?.email) throw new UserError(403, 'ログインを確認できません。開き直してください。');
        const path = new URL(request.url).pathname;
        if (path.startsWith('/api/')) {
          response = await api(request, env, fetcher);
        } else if ((path === '/' || path === '/index.html') && request.method === 'GET') {
          response = new Response(html, { headers: { 'Content-Type': 'text/html; charset=utf-8' } });
        } else {
          throw new UserError(404, 'ページが見つかりません。');
        }
      } catch (error) {
        const status = error instanceof UserError ? error.status : 500;
        const message = error instanceof UserError ? error.message : '処理に失敗しました。開き直してください。';
        response = json({ error: message, requestId }, status);
      }
      // 問い合わせ時は画面の確認番号から追跡できます。秘密情報は記録しません。
      console.log(JSON.stringify({ event: 'admin_request', requestId,
        method: request.method, status: response.status, elapsedMs: Date.now() - start }));
      return secureResponse(response, requestId);
    },
  };
}
