// Cloudflareから5分ごとにGitHubのランキング監視を起動します。
// 認証用の鍵・応答本文はログへ出さず、結果コードだけ残します。
const API = 'https://api.github.com/repos/nekoromme/mixch-archive-monitor-public';
export async function runSchedule(env, fetcher = (...args) => fetch(...args)) {
  const token = typeof env.GITHUB_TOKEN === 'string' ? env.GITHUB_TOKEN.trim() : '';
  if (!/^[A-Za-z0-9_]+$/.test(token)) throw new Error('SCHEDULER_TOKEN_FORMAT');
  const headers = { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28', 'User-Agent':'mixch-monitor-scheduler', 'Content-Type':'application/json' };
  async function request(path, body) {
    const response = await fetcher(API + path, {method:body ? 'POST':'GET', headers,
      ...(body ? {body:JSON.stringify(body)} : {}), redirect:'manual', signal:AbortSignal.timeout(15000)});
    if (!response.ok) throw new Error('SCHEDULER_GITHUB_' + response.status);
    return response.status === 204 ? null : response.json();
  }
  const file = await request('/contents/monitor_settings.json?ref=main');
  const settings = JSON.parse(atob(file.content.replace(/\s/g,'')));
  // 移行準備中は通知しない確認実行だけを許可します。本番は二つの条件が必要。
  const probe = settings.ranking_ready === false && settings.ranking_scheduler_probe === true;
  if (!probe && !(settings.ranking_enabled === true && settings.ranking_ready === true)) return 'paused';
  const runs = await request('/actions/workflows/ranking-monitor.yml/runs?per_page=5');
  if (runs.workflow_runs.some(run => ['queued','in_progress','waiting','requested','pending'].includes(run.status))) return 'already-running';
  await request('/actions/workflows/ranking-monitor.yml/dispatches', {
    ref:'main', inputs:{dry_run:String(probe),test_webhook:'false'},
  });
  return probe ? 'probe-dispatched' : 'dispatched';
}
// 権限不足などもこちらから切り分けられるよう、結果が変わった時だけ保存。
// 毎回コミットせず、監視履歴・管理画面の設定には触れません。
async function recordHealth(env, result) {
  const headers = { Authorization:'Bearer ' + env.GITHUB_TOKEN.trim(),
    'User-Agent':'mixch-monitor-scheduler', 'Content-Type':'application/json', Accept:'application/vnd.github+json' };
  const url = API + '/contents/ranking_scheduler_health.json';
  const previous = await fetch(url + '?ref=main', {headers,redirect:'manual',signal:AbortSignal.timeout(10000)});
  let sha;
  if (previous.ok) {
    const file = await previous.json();
    const old = JSON.parse(atob(file.content.replace(/\s/g,'')));
    if (old.result === result) return;
    sha = file.sha;
  } else if (previous.status !== 404) return;
  const response = await fetch(url, {method:'PUT',headers,redirect:'manual',signal:AbortSignal.timeout(10000),
    body:JSON.stringify({branch:'main',sha,message:'Record ranking scheduler health [skip ci]',
      content:btoa(JSON.stringify({result,changed_at:new Date().toISOString()},null,2)+'\n')})});
  if (!response.ok) console.error('SCHEDULER_HEALTH_WRITE_FAILED');
}
export async function scheduled(controller, env) {
  try {
    const result = await runSchedule(env);
    console.log(JSON.stringify({event:'ranking_schedule',result}));
    try { await recordHealth(env,result); } catch { console.error('SCHEDULER_HEALTH_WRITE_FAILED'); }
  }
  catch (error) {
    // 接続ライブラリの例外に秘密情報が混ざっていても公開しません。
    const code = /^SCHEDULER_[A-Z_0-9]+$/.test(error.message) ? error.message : 'SCHEDULER_NETWORK';
    console.error(JSON.stringify({event:'ranking_schedule',result:code}));
    try { await recordHealth(env,code); } catch { console.error('SCHEDULER_HEALTH_WRITE_FAILED'); }
    throw new Error(code);
  }
}
