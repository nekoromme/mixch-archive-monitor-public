import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { createApp } from '../src/app.js';

const context = { access: { getIdentity: async () => ({ email: 'owner@example.test' }) } };
const env = { GITHUB_TOKEN: 'test_secret_never_print',
  ADMIN_PASSWORD: 'testing-password-very-long-123',
  LOGIN_LIMITER: { limit: async () => ({ success: true }) } };
const origin = 'https://admin.example.test';
const target = { index: 0, id: '123', name: 'テスト' };

test('接続確認は本人の操作で通知なしの実行だけを行い、定期実行と別に記録する', async () => {
  let ready = false;
  let calls = 0;
  let dispatches = 0;
  let health;
  const app = createApp('<title>管理</title>',async (url, options) => {
    calls++;
    if (url.includes('/contents/monitor_settings.json')) return Response.json({content:Buffer.from(JSON.stringify({ranking_enabled:true,ranking_ready:ready,ranking_scheduler_probe:true})).toString('base64')});
    if (url.includes('/runs?')) return Response.json({workflow_runs:[]});
    if (url.endsWith('/dispatches')) {
      dispatches++;
      assert.deepEqual(JSON.parse(options.body).inputs,{dry_run:'true',test_webhook:'false'});
      return new Response(null,{status:204});
    }
    assert.ok(url.includes('/contents/ranking_connection_health.json'));
    if (options.method !== 'PUT') return new Response(null,{status:404});
    health=JSON.parse(Buffer.from(JSON.parse(options.body).content,'base64').toString());
    return Response.json({ok:true});
  });
  const send = (cookie, requestOrigin=origin) => app.fetch(new Request(origin+'/api/ranking-probe',{
    method:'POST',headers:{Cookie:cookie,Origin:requestOrigin,'Content-Type':'application/json'},body:'{}',
  }),env,{});
  assert.equal((await send('')).status,401);
  assert.equal(calls,0);
  const cookie = await login(app);
  assert.equal((await send(cookie,'https://other.example.test')).status,403);
  assert.equal(calls,0);
  const response = await send(cookie);
  assert.equal(response.status,200);
  assert.equal((await response.json()).result,'probe-dispatched');
  assert.equal(dispatches,1);
  assert.equal(health.result,'probe-dispatched');
  ready=true;
  assert.equal((await send(cookie)).status,409);
  assert.equal(dispatches,1);
});

// 外部への接続なしで、実際と同じGET→編集→PUTの流れを通します。
function fixture() {
  let rows = [{ id: '123', name: 'テスト', extra: 'preserve' }];
  let version = 'version-1';
  let calls = 0;
  let writes = 0;
  let conflictOnWrite = false;
  const fetcher = async (url, options) => {
    calls++;
    assert.match(String(url), /^https:\/\/api.github.com\/repos\/nekoromme\/mixch-archive-monitor-public\/contents\/watchlist.json/);
    assert.equal(options.headers.Authorization, 'Bearer ' + env.GITHUB_TOKEN);
    if (options.method === 'GET') return Response.json({
      sha: version, content: Buffer.from(JSON.stringify(rows)).toString('base64'),
    });
    const body = JSON.parse(options.body);
    assert.equal(body.sha, version);
    if (conflictOnWrite) return Response.json({}, { status: 409 });
    rows = JSON.parse(Buffer.from(body.content, 'base64').toString('utf8'));
    version = 'version-' + (++writes + 1);
    return Response.json({ content: { sha: version } });
  };
  const app = createApp('<!doctype html><title>管理</title>', fetcher);
  let session;
  const send = async (path, body, options = {}) => {
    session ||= await login(app);
    return app.fetch(new Request(origin + path, {
    method: body ? 'POST' : 'GET',
    headers: { Cookie: session, ...(body ? { Origin: origin, 'Content-Type': 'application/json' } : {}),
      ...options.headers },
    ...(body ? { body: JSON.stringify({ version, ...body }),
       } : {}),
  }), options.env || env, options.context || context);
  };
  return { app, send, get rows() { return rows; }, get calls() { return calls; },
    get writes() { return writes; }, conflict() { conflictOnWrite = true; } };
}

async function login(app, password = env.ADMIN_PASSWORD) {
  const response = await app.fetch(new Request(origin + '/api/login', {
    method: 'POST', headers: { Origin: origin, 'Content-Type': 'application/json' },
    body: JSON.stringify({ password }),
  }), env, {});
  assert.equal(response.status, 200);
  const cookie = response.headers.get('Set-Cookie');
  assert.match(cookie, /HttpOnly; Secure; SameSite=Lax/);
  return cookie.split(';')[0];
}

test('認証なし・偽の認証ヘッダーでは保存できずログイン画面になる', async () => {
  const f = fixture();
  for (const path of ['/', '/api/watchlist', '/api/update']) {
    const response = await f.app.fetch(new Request(origin + path, {
      headers: { 'Cf-Access-Authenticated-User-Email': 'owner@example.test',
        'Cf-Access-Jwt-Assertion': 'forged' },
    }), env, {});
    assert.equal(response.status, path === '/' ? 200 : 401);
    if (path === '/') assert.match(await response.text(), /ログイン/);
  }
  assert.equal(f.calls, 0);
});

test('認証済みの画面は表示でき、秘密の鍵を含めない', async () => {
  const f = fixture();
  const response = await f.send('/');
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('Cache-Control'), 'no-store');
  assert.equal(response.headers.get('X-Frame-Options'), 'DENY');
  assert.ok(!(await response.text()).includes(env.GITHUB_TOKEN));
});

test('個別編集は名前を変更し他の項目を保持、古い個別フラグを除去', async () => {
  const f = fixture();
  assert.equal((await (await f.send('/api/watchlist')).json()).items[0].archiveEnabled, true);
  let response = await f.send('/api/update', {
    target, streamer: { id: '123', name: 'テスト', archive_enabled: false },
  });
  assert.equal(response.status, 200);
  assert.equal(f.rows[0].extra, 'preserve');
  assert.equal((await (await f.send('/api/watchlist')).json()).items[0].archiveEnabled, true);
  response = await f.send('/api/update', {
    target, streamer: { id: '123', name: 'テスト😊', archive_enabled: true },
  });
  assert.equal(response.status, 200);
  assert.equal(f.rows[0].name, 'テスト😊');
  assert.equal(f.rows[0].archive_enabled, undefined);
});

test('URLから追加、名前取得待ちの形式を維持、削除も可能', async () => {
  const f = fixture();
  assert.equal((await f.send('/api/add', { streamer: { id: 'https://mixch.tv/u/456/live_archives' } })).status, 200);
  assert.deepEqual(f.rows[1], { id: '456', name: '__AUTO_NAME__:456' });
  assert.equal((await f.send('/api/delete', { target })).status, 200);
  assert.equal(f.rows.length, 1);
  assert.equal(f.rows[0].id, '456');
});

test('古い画面・同時保存・対象の食い違いは上書きしない', async () => {
  const f = fixture();
  assert.equal((await f.send('/api/delete', { version: 'stale', target })).status, 409);
  assert.equal((await f.send('/api/delete', { target: { ...target, id: '456' } })).status, 409);
  f.conflict();
  assert.equal((await f.send('/api/delete', { target })).status, 409);
  assert.equal(f.writes, 0);
});

test('他サイトからの送信と不正な設定・重複登録を拒否', async () => {
  const f = fixture();
  assert.equal((await f.send('/api/delete', { target }, { headers: { Origin: 'https://other.test' } })).status, 403);
  assert.equal(f.calls, 0);
  assert.equal((await f.send('/api/update', { target,
    streamer: { id: '123', name: '' } })).status, 400);
  assert.equal((await f.send('/api/add', { streamer: { id: '123' } })).status, 400);
  assert.equal(f.writes, 0);
});

test('未設定・上流障害では秘密情報を表示しない', async () => {
  const f = fixture();
  assert.equal((await f.send('/api/watchlist', null, { env: { ...env, GITHUB_TOKEN: '' } })).status, 503);
  const app = createApp('', async () => { throw new Error(env.GITHUB_TOKEN); });
  const response = await app.fetch(new Request(origin + '/api/watchlist', {
    headers: { Cookie: await login(app) },
  }), env, context);
  assert.equal(response.status, 502);
  assert.ok(!(await response.text()).includes(env.GITHUB_TOKEN));
});

test('移行画面にGAS依存や外部スクリプトがなく、JavaScript構文が正しい', () => {
  const html = readFileSync(new URL('../public/index.html', import.meta.url), 'utf8');
  assert.ok(!html.includes('google.script'));
  assert.ok(!html.includes('<script src='));
  assert.ok(html.includes('id="globalMonitor"'));
  assert.ok(!html.includes('id="archiveToggle"'));
  new vm.Script(html.match(/<script>([\s\S]*?)<\/script>/)[1]);
});

test('間違ったパスワード・別サイト送信・連続試行を拒否', async () => {
  const { app } = fixture();
  const request = (password, from = origin) => new Request(origin + '/api/login', {
    method: 'POST', headers: {Origin: from, 'Content-Type': 'application/json'},
    body: JSON.stringify({password}),
  });
  assert.equal((await app.fetch(request('wrong-password'), env, {})).status, 401);
  assert.equal((await app.fetch(request(env.ADMIN_PASSWORD, 'https://evil.test'), env, {})).status, 403);
  const limited = {...env, LOGIN_LIMITER: {limit: async () => ({success:false})}};
  assert.equal((await app.fetch(request(env.ADMIN_PASSWORD), limited, {})).status, 429);
  assert.equal((await app.fetch(request(env.ADMIN_PASSWORD), {...env, LOGIN_LIMITER:null}, {})).status, 503);
});

test('パスワード未設定・空欄・長すぎる設定では保護を解除しない', async () => {
  const {app} = fixture();
  for (const password of [undefined, '', 'x'.repeat(257)]) {
    assert.equal((await app.fetch(new Request(origin + '/'), {...env, ADMIN_PASSWORD:password}, {})).status, 503);
  }
});

test('改ざん・期限切れ・別サイトの証明・パスワード変更を検出', async () => {
  const {app} = fixture();
  const session = await login(app);
  const response = async (cookie, settings = env, host = origin) => app.fetch(new Request(host + '/api/watchlist', {
    headers:{Cookie:cookie},
  }), settings, {});
  assert.equal((await response(session + 'x')).status, 401);
  assert.equal((await response(session, {...env, ADMIN_PASSWORD:'replacement-password-123456'})).status, 401);
  assert.equal((await response(session, env, 'https://another.test')).status, 401);
  const originalNow = Date.now;
  try {
    Date.now = () => originalNow() + 31 * 24 * 60 * 60 * 1000;
    assert.equal((await response(session)).status, 401);
  } finally { Date.now = originalNow; }
});

test('ログアウトはブラウザの証明を消す', async () => {
  const {app} = fixture();
  const session = await login(app);
  const response = await app.fetch(new Request(origin + '/api/logout', {
    method:'POST', headers:{Origin:origin, Cookie:session},
  }), env, {});
  assert.equal(response.status, 200);
  assert.match(response.headers.get('Set-Cookie'), /Max-Age=0/);
  assert.equal((await app.fetch(new Request(origin + '/api/watchlist'), env, {})).status, 401);
});

test('ログイン画面のJavaScript構文を確認', () => {
  const html = readFileSync(new URL('../public/login.html', import.meta.url), 'utf8');
  new vm.Script(html.match(/<script>([\s\S]*?)<\/script>/)[1]);
  assert.ok(html.includes('autocomplete="current-password"'));
  assert.ok(!html.includes(env.ADMIN_PASSWORD));
});

test('1文字のパスワードでもログインできる', async () => {
  const {app} = fixture();
  const response = await app.fetch(new Request(origin + '/api/login', {
    method:'POST', headers:{Origin:origin, 'Content-Type':'application/json'},
    body:JSON.stringify({password:'a'}),
  }), {...env, ADMIN_PASSWORD:'a'}, {});
  assert.equal(response.status, 200);
  assert.ok(response.headers.get('Set-Cookie'));
});


test('全体を停止・再開し、古い設定や別サイトからの操作を拒否', async () => {
  let enabled = true, version = 'settings-1', writes = 0;
  const app = createApp('', async (url, options) => {
    assert.ok(url.includes('/contents/monitor_settings.json'));
    if (options.method === 'GET') return Response.json({sha:version,content:Buffer.from(JSON.stringify({enabled})).toString('base64')});
    const body = JSON.parse(options.body);
    assert.equal(body.sha, version);
    enabled = JSON.parse(Buffer.from(body.content,'base64').toString()).enabled;
    version = 'settings-' + (++writes + 1);
    return Response.json({});
  });
  const cookie = await login(app);
  const send = (body, site = origin) => app.fetch(new Request(origin + '/api/monitoring', {
    method:body ? 'POST':'GET', headers:{Cookie:cookie,Origin:site,'Content-Type':'application/json'},
    ...(body ? {body:JSON.stringify(body)} : {}),
  }),env,{});
  assert.equal((await (await send()).json()).enabled,true);
  assert.equal((await send({enabled:false,version})).status,200);
  assert.equal((await (await send()).json()).enabled,false);
  assert.equal((await send({enabled:true,version:'stale'})).status,409);
  assert.equal((await send({enabled:true,version},'https://other.test')).status,403);
  assert.equal(enabled,false);
  assert.equal((await send({enabled:true,version})).status,200);
  assert.equal((await (await send()).json()).enabled,true);
});


test('アーカイブとランキングを独立保存し、未指定の設定も維持する', async () => {
  let settings={enabled:false,ranking_enabled:true,ranking_ready:true,extra:'keep'}, version='v1';
  const app=createApp('',async (url,options)=>{
    if(options.method==='GET') return Response.json({sha:version,content:Buffer.from(JSON.stringify(settings)).toString('base64')});
    const body=JSON.parse(options.body);assert.equal(body.sha,version);
    settings=JSON.parse(Buffer.from(body.content,'base64').toString());version+='x';return Response.json({});
  });
  const cookie=await login(app);
  const send=body=>app.fetch(new Request(origin+'/api/monitoring',{
    method:'POST',headers:{Cookie:cookie,Origin:origin,'Content-Type':'application/json'},body:JSON.stringify({...body,version})
  }),env,{});
  assert.equal((await send({kind:'ranking',enabled:false})).status,200);
  assert.equal(settings.enabled,false);assert.equal(settings.ranking_enabled,false);
  assert.equal(settings.extra,'keep');assert.ok(settings.ranking_changed_at);
  assert.equal((await send({kind:'archive',enabled:true})).status,200);
  assert.equal(settings.ranking_enabled,false);assert.equal(settings.enabled,true);
  assert.equal((await send({kind:'ranking',enabled:true})).status,200);
  assert.equal(settings.enabled,true);assert.equal(settings.ranking_enabled,true);
  settings.ranking_ready=false;
  assert.equal((await send({kind:'ranking',enabled:false})).status,409);
  assert.equal(settings.ranking_enabled,true);
});
