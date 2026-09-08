import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { createApp } from '../src/app.js';

const context = { access: { getIdentity: async () => ({ email: 'owner@example.test' }) } };
const env = { GITHUB_TOKEN: 'test-secret-never-print' };
const origin = 'https://admin.example.test';
const target = { index: 0, id: '123', name: 'テスト' };

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
  const send = (path, body, options = {}) => app.fetch(new Request(origin + path, {
    method: body ? 'POST' : 'GET',
    ...(body ? { body: JSON.stringify({ version, ...body }),
      headers: { Origin: origin, 'Content-Type': 'application/json', ...options.headers } } : {}),
  }), options.env || env, options.context || context);
  return { app, send, get rows() { return rows; }, get calls() { return calls; },
    get writes() { return writes; }, conflict() { conflictOnWrite = true; } };
}

test('認証なし・偽の認証ヘッダーは画面も保存も拒否', async () => {
  const f = fixture();
  for (const path of ['/', '/api/watchlist', '/api/update']) {
    const response = await f.app.fetch(new Request(origin + path, {
      headers: { 'Cf-Access-Authenticated-User-Email': 'owner@example.test',
        'Cf-Access-Jwt-Assertion': 'forged' },
    }), env, {});
    assert.equal(response.status, 403);
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

test('既存対象はオン、オフ保存→再読込→オン復帰で他の項目も保持', async () => {
  const f = fixture();
  assert.equal((await (await f.send('/api/watchlist')).json()).items[0].archiveEnabled, true);
  let response = await f.send('/api/update', {
    target, streamer: { id: '123', name: 'テスト', archive_enabled: false },
  });
  assert.equal(response.status, 200);
  assert.equal(f.rows[0].extra, 'preserve');
  assert.equal((await (await f.send('/api/watchlist')).json()).items[0].archiveEnabled, false);
  response = await f.send('/api/update', {
    target, streamer: { id: '123', name: 'テスト😊', archive_enabled: true },
  });
  assert.equal(response.status, 200);
  assert.equal(f.rows[0].name, 'テスト😊');
  assert.equal(f.rows[0].archive_enabled, true);
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
    streamer: { id: '123', name: 'テスト', archive_enabled: 'false' } })).status, 400);
  assert.equal((await f.send('/api/add', { streamer: { id: '123' } })).status, 400);
  assert.equal(f.writes, 0);
});

test('未設定・上流障害では秘密情報を表示しない', async () => {
  const f = fixture();
  assert.equal((await f.send('/api/watchlist', null, { env: {} })).status, 503);
  const app = createApp('', async () => { throw new Error(env.GITHUB_TOKEN); });
  const response = await app.fetch(new Request(origin + '/api/watchlist'), env, context);
  assert.equal(response.status, 502);
  assert.ok(!(await response.text()).includes(env.GITHUB_TOKEN));
});

test('移行画面にGAS依存や外部スクリプトがなく、JavaScript構文が正しい', () => {
  const html = readFileSync(new URL('../public/index.html', import.meta.url), 'utf8');
  assert.ok(!html.includes('google.script'));
  assert.ok(!html.includes('<script src='));
  assert.ok(html.includes('id="archiveToggle"'));
  new vm.Script(html.match(/<script>([\s\S]*?)<\/script>/)[1]);
});
