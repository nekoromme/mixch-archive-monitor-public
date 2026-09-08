// 個人用パスワード認証。パスワードはCloudflareのSecretだけに保存します。
// ブラウザへはパスワードを含まない、署名付きのログイン証明を渡します。
const COOKIE = '__Host-mixch_session';
const LIFETIME = 30 * 24 * 60 * 60;
const encoder = new TextEncoder();

function result(value, status = 200, headers = {}) {
  // Safariで直接開いた場合も、日本語をUTF-8として表示します。
  return Response.json(value, { status, headers: { 'Content-Type': 'application/json; charset=utf-8', ...headers } });
}
function base64url(bytes) {
  let value = '';
  for (const byte of bytes) value += String.fromCharCode(byte);
  return btoa(value).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
function decode(value) {
  if (!/^[A-Za-z0-9_-]+$/.test(value)) throw new Error('Invalid encoding');
  return Uint8Array.from(atob(value.replace(/-/g, '+').replace(/_/g, '/')), c => c.charCodeAt(0));
}
async function signingKey(password) {
  return crypto.subtle.importKey('raw', encoder.encode('mixch-session-v1:' + password),
    { name: 'HMAC', hash: 'SHA-256' }, false, ['sign', 'verify']);
}
function cookie(value, age = LIFETIME) {
  return COOKIE + '=' + value + '; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=' + age;
}
async function createSession(key, origin) {
  const payload = base64url(encoder.encode(JSON.stringify({
    exp: Math.floor(Date.now() / 1000) + LIFETIME, origin, nonce: crypto.randomUUID(),
  })));
  const signature = await crypto.subtle.sign('HMAC', key, encoder.encode(payload));
  return payload + '.' + base64url(new Uint8Array(signature));
}
async function validSession(request, key) {
  try {
    const pair = (request.headers.get('Cookie') || '').split(';')
      .map(part => part.trim()).find(part => part.startsWith(COOKIE + '='));
    if (!pair) return false;
    const token = pair.slice(COOKIE.length + 1);
    if (token.length > 2048) return false;
    const parts = token.split('.');
    if (parts.length !== 2) return false;
    const [payload, signature] = parts;
    if (!await crypto.subtle.verify('HMAC', key, decode(signature), encoder.encode(payload))) return false;
    const data = JSON.parse(new TextDecoder().decode(decode(payload)));
    const now = Math.floor(Date.now() / 1000);
    return Number.isInteger(data.exp) && data.exp > now && data.exp <= now + LIFETIME + 60 &&
      data.origin === new URL(request.url).origin;
  } catch { return false; }
}

// 認証済みの場合のみnullを返す。それ以外はここで応答して保存処理へ進ませません。
export async function authorize(request, env, loginHtml, readBody) {
  if (typeof env.ADMIN_PASSWORD !== 'string' ||
      env.ADMIN_PASSWORD.length < 1 || env.ADMIN_PASSWORD.length > 256) {
    // 管理画面を開いた人には、機械向けのJSONではなく設定案内を表示します。
    if (request.method === 'GET' && ['/', '/index.html'].includes(new URL(request.url).pathname)) {
      return new Response(`<!doctype html><html lang="ja"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>パスワードの設定が必要です</title>
<body style="font:18px/1.8 system-ui;padding:24px;max-width:640px;margin:auto">
<h1 style="font-size:24px">パスワードの設定が必要です</h1>
<p>Cloudflareの管理画面で、このアプリの専用パスワードを登録してください。</p>
<ol><li>mixch-archive-admin → Settings → Variables and Secrets を開く</li>
<li>Add variable を押す</li><li>Key に <strong>ADMIN_PASSWORD</strong>、Value に好きなパスワード（1～256文字）を入力する</li>
<li>Secret にチェックを入れ、追加して Deploy で反映する</li></ol>
<p>登録済みの場合は、名前が ADMIN_PASSWORD と完全に一致するか、実行用の設定に保存したか確認してください。</p>
<p>設定が反映されたら、このページを再読み込みするとログイン画面になります。</p>
<button onclick="location.reload()" style="font:inherit;padding:12px">再読み込み</button></body></html>`,
        { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } });
    }
    return result({ error: '初期設定が未完了です。CloudflareのSecretにADMIN_PASSWORDを1～256文字で登録してください。' }, 503);
  }
  const url = new URL(request.url);
  const key = await signingKey(env.ADMIN_PASSWORD);
  if (url.pathname === '/api/login' || url.pathname === '/api/logout') {
    if (request.method !== 'POST') return result({ error: '送信方法が不正です。' }, 405);
    if (request.headers.get('Origin') !== url.origin) return result({ error: '送信元を確認できません。' }, 403);
    if (url.pathname === '/api/logout') {
      return result({ ok: true }, 200, { 'Set-Cookie': cookie('', 0) });
    }
    // 連続試行をCloudflare側で抑制。設定漏れでも無制限の認証へ切り替えません。
    if (!env.LOGIN_LIMITER) return result({ error: 'ログイン設定の反映待ちです。公開処理の完了を確認してください。' }, 503);
    const { success } = await env.LOGIN_LIMITER.limit({ key: 'mixch-admin-password-login' });
    if (!success) return result({ error: '試行回数が多いため、1分ほど待ってからやり直してください。' }, 429, { 'Retry-After': '60' });
    const body = await readBody(request);
    if (typeof body.password !== 'string' || body.password.length > 256) {
      return result({ error: 'パスワードが違います。' }, 401);
    }
    // 文字列の先頭から比較せず、暗号ライブラリで検証します。
    const proof = await crypto.subtle.sign('HMAC', key, encoder.encode('login:' + env.ADMIN_PASSWORD));
    if (!await crypto.subtle.verify('HMAC', key, proof, encoder.encode('login:' + body.password))) {
      return result({ error: 'パスワードが違います。' }, 401);
    }
    return result({ ok: true }, 200, { 'Set-Cookie': cookie(await createSession(key, url.origin)) });
  }
  if (await validSession(request, key)) return null;
  if (request.method === 'GET' && ['/', '/index.html'].includes(url.pathname)) {
    return new Response(loginHtml, { headers: { 'Content-Type': 'text/html; charset=utf-8' } });
  }
  return result({ error: 'ログインが必要です。ページを開き直してください。' }, 401);
}
