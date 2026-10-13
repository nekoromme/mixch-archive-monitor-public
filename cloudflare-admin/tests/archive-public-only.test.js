import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { createApp } from '../src/app.js';

test('全体公開だけの保存・再読込みは監視停止やランキング設定を保つ', async () => {
  const origin = 'https://admin.example.test';
  const env = { GITHUB_TOKEN: 'test_only', ADMIN_PASSWORD: 'test-password',
    LOGIN_LIMITER: { limit: async () => ({ success: true }) } };
  let settings = { enabled: false, ranking_ready: false, ranking_recording_enabled: true,
    ranking_changed_at: 'keep-this-time', extra: 'keep' }, version = 'v1';
  const app = createApp('', async (_url, options) => {
    if (options.method === 'GET') return Response.json({ sha: version,
      content: Buffer.from(JSON.stringify(settings)).toString('base64') });
    const body = JSON.parse(options.body);
    assert.equal(body.sha, version);
    settings = JSON.parse(Buffer.from(body.content, 'base64').toString());
    version += 'x';
    return Response.json({ content: { sha: version } });
  });
  const login = await app.fetch(new Request(origin + '/api/login', {
    method: 'POST', headers: { Origin: origin, 'Content-Type': 'application/json' },
    body: JSON.stringify({ password: env.ADMIN_PASSWORD }),
  }), env, {});
  const cookie = login.headers.get('Set-Cookie').split(';')[0];
  const send = body => app.fetch(new Request(origin + '/api/monitoring', {
    method: body ? 'POST' : 'GET',
    headers: { Cookie: cookie, Origin: origin, 'Content-Type': 'application/json' },
    ...(body ? { body: JSON.stringify({ version, kind: 'publicOnly', ...body }) } : {}),
  }), env, {});
  assert.equal((await (await send()).json()).archivePublicOnly, false);
  for (const enabled of [true, false]) {
    assert.equal((await send({ enabled })).status, 200);
    assert.equal((await (await send()).json()).archivePublicOnly, enabled);
    assert.equal(settings.enabled, false);
    assert.equal(settings.ranking_recording_enabled, true);
    assert.equal(settings.ranking_changed_at, 'keep-this-time');
    assert.equal(settings.extra, 'keep');
  }
  assert.equal((await send({ enabled: true, version: 'stale' })).status, 409);
  assert.equal((await send({ enabled: 'true' })).status, 400);
  settings.archive_public_only = 'false';
  assert.equal((await send()).status, 502);
});

test('画面のボタンで全体公開だけを保存し、再読込み後も選択が一致する', async () => {
  let monitoring = { enabled: false, archivePublicOnly: true,
    rankingReady: false, rankingEnabled: false, rankingRecordingEnabled: true, version: 'v1' };
  const nodes = new Map();
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, { textContent: '', innerHTML: '', hidden: false,
      style: {}, attributes: {}, setAttribute(k, v) { this.attributes[k] = v; },
      addEventListener() {}, querySelectorAll: () => [] });
    return nodes.get(id);
  };
  const context = vm.createContext({ Response, AbortSignal, URLSearchParams, console,
    document: { getElementById: node, addEventListener() {}, querySelectorAll: () => [] },
    window: { addEventListener() {} }, localStorage: { getItem: () => null },
    fetch: async (url, options) => {
      assert.equal(url, '/api/monitoring');
      if (options.method === 'POST') {
        const body = JSON.parse(options.body);
        assert.equal(body.kind, 'publicOnly');
        assert.equal(body.version, monitoring.version);
        monitoring = { ...monitoring, archivePublicOnly: body.enabled, version: monitoring.version + 'x' };
        return Response.json({ ok: true });
      }
      return Response.json(monitoring);
    },
  });
  const html = readFileSync(new URL('../public/index.html', import.meta.url), 'utf8');
  new vm.Script(html.match(/<script>([\s\S]*?)<\/script>/)[1]).runInContext(context);
  node('formScreen').hidden = true;
  await vm.runInContext('loadMonitoring()', context);
  assert.match(node('archivePublicOnly').textContent, /全体公開だけ（解除する）/);
  assert.equal(node('archivePublicOnly').attributes['aria-pressed'], 'true');
  await vm.runInContext("toggleGlobalMonitor('publicOnly')", context);
  assert.equal(monitoring.archivePublicOnly, false);
  assert.equal(monitoring.enabled, false);
  await vm.runInContext('loadMonitoring()', context);
  assert.match(node('archivePublicOnly').textContent, /絞り込みなし/);
  await vm.runInContext("toggleGlobalMonitor('publicOnly')", context);
  assert.equal(monitoring.archivePublicOnly, true);
  assert.match(node('monitoringState').textContent, /公開範囲：全体公開だけ/);
});
