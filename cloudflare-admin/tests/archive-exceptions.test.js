import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { createApp, monthEndJst } from '../src/app.js';

const origin = 'https://admin.example.test';
const env = { GITHUB_TOKEN: 'test_only', ADMIN_PASSWORD: 'test-password',
  LOGIN_LIMITER: { limit: async () => ({ success: true }) } };

async function fixture(t, initial = [{ id: '123', name: '既存', extra: 'keep' }]) {
  t.mock.timers.enable({ apis: ['Date'], now: new Date('2026-10-25T12:00:00Z') });
  let rows = structuredClone(initial), version = 'v1', writes = 0, conflict = false;
  const app = createApp('', async (_url, options) => {
    if (options.method === 'GET') return Response.json({ sha: version,
      content: Buffer.from(JSON.stringify(rows)).toString('base64') });
    if (conflict) {
      conflict = false;
      rows.push({ id: '999', name: '同時追加' });
      version += 'x';
      return Response.json({}, { status: 409 });
    }
    const body = JSON.parse(options.body);
    assert.equal(body.sha, version);
    rows = JSON.parse(Buffer.from(body.content, 'base64').toString());
    version += 'x'; writes++;
    return Response.json({ content: { sha: version } });
  });
  const login = await app.fetch(new Request(origin + '/api/login', { method: 'POST',
    headers: { Origin: origin, 'Content-Type': 'application/json' },
    body: JSON.stringify({ password: env.ADMIN_PASSWORD }) }), env, {});
  const cookie = login.headers.get('Set-Cookie').split(';')[0];
  const send = (path, body, headers = {}) => app.fetch(new Request(origin + path, {
    method: body ? 'POST' : 'GET', headers: { Cookie: cookie, Origin: origin,
      'Content-Type': 'application/json', ...headers },
    ...(body ? { body: JSON.stringify({ version, ...body }) } : {}),
  }), env, {});
  return { send, get rows() { return rows; }, get writes() { return writes; },
    concurrentAdd() { conflict = true; } };
}

test('登録月末は日本時間。月末・年末・うるう年でも30日後にはしない', () => {
  for (const [date, expected] of [
    ['2026-10-25T00:00:00Z', '2026-10-31'],
    ['2026-10-31T14:59:59Z', '2026-10-31'],
    ['2026-10-31T15:00:00Z', '2026-11-30'],
    ['2026-12-31T15:00:00Z', '2027-01-31'],
    ['2028-02-20T00:00:00Z', '2028-02-29'],
    ['2027-02-20T00:00:00Z', '2027-02-28'],
  ]) assert.equal(monthEndJst(new Date(date)), expected);
});

test('ID/URLで新規・既存に例外登録、重複せず再読込でき、解除しても通常登録は残る', async t => {
  const f = await fixture(t);
  for (const id of ['123', 'https://mixch.tv/u/456/live_archives?from=share', 'https://mixch.tv/u/123']) {
    assert.equal((await f.send('/api/archive-exception', { streamer: { id }, enabled: true })).status, 200);
  }
  assert.equal(f.rows.length, 2);
  assert.deepEqual(f.rows[0], { id: '123', name: '既存', extra: 'keep', archive_all_until: '2026-10-31' });
  assert.equal(f.rows[1].name, '__AUTO_NAME__:456');
  const read = await (await f.send('/api/watchlist')).json();
  assert.equal(read.items[1].archiveAllUntil, '2026-10-31');
  assert.equal((await f.send('/api/archive-exception', { streamer: { id: '123' }, enabled: false })).status, 200);
  assert.deepEqual(f.rows[0], { id: '123', name: '既存', extra: 'keep' });
  assert.equal(f.rows[1].archive_all_until, '2026-10-31');
});

test('翌月に読み込むと期限切れだけ保存先から除去し、同時追加を保持する', async t => {
  const f = await fixture(t, [
    { id: '123', name: '既存', archive_all_until: '2026-10-31', extra: 'keep' },
    { id: '456', name: '次の月', archive_all_until: '2026-11-30' },
  ]);
  t.mock.timers.setTime(new Date('2026-10-31T14:59:59Z').getTime());
  assert.equal((await (await f.send('/api/watchlist')).json()).items[0].archiveAllUntil, '2026-10-31');
  assert.equal(f.writes, 0);
  t.mock.timers.setTime(new Date('2026-10-31T15:00:00Z').getTime());
  f.concurrentAdd();
  const read = await (await f.send('/api/watchlist')).json();
  assert.equal(read.items[0].archiveAllUntil, null);
  assert.equal(read.items[1].archiveAllUntil, '2026-11-30');
  assert.equal(read.items[2].id, '999');
  assert.deepEqual(f.rows[0], { id: '123', name: '既存', extra: 'keep' });
  await f.send('/api/watchlist');
  assert.equal(f.writes, 1);
});

test('不正入力・別サイト・古い版を拒否し、ID変更には例外を引き継がない', async t => {
  const f = await fixture(t, [{ id: '123', name: '既存', archive_all_until: '2026-10-31' }]);
  const body = { streamer: { id: '123' }, enabled: true };
  assert.equal((await f.send('/api/archive-exception', { ...body, version: 'stale' })).status, 409);
  assert.equal((await f.send('/api/archive-exception', body, { Origin: 'https://other.test' })).status, 403);
  assert.equal((await f.send('/api/archive-exception', { ...body, enabled: 'true' })).status, 400);
  assert.equal((await f.send('/api/archive-exception', { ...body, streamer: { id: 'https://evil.test/u/123' } })).status, 400);
  assert.equal(f.writes, 0);
  assert.equal((await f.send('/api/update', { target: { index: 0, id: '123', name: '既存' },
    streamer: { id: '456', name: '別人' } })).status, 200);
  assert.equal(f.rows[0].archive_all_until, undefined);
});

test('壊れた期限を読み込んでも一覧を消したり全通知に広げない', async t => {
  const f = await fixture(t, [{ id: '123', name: '既存', archive_all_until: '2026-02-31' }]);
  assert.equal((await f.send('/api/watchlist')).status, 502);
  assert.equal(f.writes, 0);
});

test('折りたたみから通常と同じ登録フォームでID/URL登録し、一覧から例外だけ解除できる', async () => {
  const html = readFileSync(new URL('../public/index.html', import.meta.url), 'utf8');
  assert.match(html, /<details id="archiveExceptions"[^>]*>/);
  assert.doesNotMatch(html.match(/<details id="archiveExceptions"[^>]*>/)[0], /\sopen(?:\s|>)/);
  const nodes = new Map();
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, { value: '', textContent: '', innerHTML: '', hidden: true,
      style: {}, classList: { add() {}, remove() {} }, setAttribute() {}, addEventListener() {},
      focus() {}, querySelectorAll: () => [] });
    return nodes.get(id);
  };
  let sent;
  const context = vm.createContext({ Response, AbortSignal, URLSearchParams, console,
    document: { getElementById: node, addEventListener() {}, querySelectorAll: () => [],
      body: { classList: { add() {}, remove() {} } } },
    window: { addEventListener() {}, setTimeout() {}, clearTimeout() {} },
    localStorage: { getItem: () => null }, confirm: () => true,
  });
  new vm.Script(html.match(/<script>([\s\S]*?)<\/script>/)[1]).runInContext(context);
  context.capture = value => { sent = value; };
  // 通信先の呼出しを記録し、画面側の経路・入力を確かめる。
  vm.runInContext(`adminApi = () => {
    const runner = { withSuccessHandler(){ return runner; }, withFailureHandler(){ return runner; },
      setArchiveException(streamer, enabled){ capture({streamer, enabled}); },
      addStreamer(){ throw new Error('wrong registration path'); } }; return runner;
  }; currentVersion = 'v1';`, context);
  for (const input of ['123', 'https://mixch.tv/u/123/live_archives']) {
    vm.runInContext('loading = false; openForm(undefined, true)', context);
    assert.equal(node('formTitle').textContent, '月末まで全通知');
    assert.equal(node('streamerNameGroup').hidden, true);
    assert.equal(node('exceptionFormNote').hidden, false);
    node('streamerId').value = input;
    vm.runInContext('saveForm({preventDefault(){}})', context);
    assert.equal(sent.streamer.id, '123');
    assert.equal(sent.enabled, true);
  }
  vm.runInContext("loading = false; removeArchiveException('123')", context);
  assert.equal(sent.enabled, false);
  vm.runInContext('loading = false; openForm()', context);
  assert.equal(node('formTitle').textContent, '新規追加');
  assert.equal(node('exceptionFormNote').hidden, true);
});
