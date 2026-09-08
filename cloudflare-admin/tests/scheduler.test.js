import {test} from 'node:test';
import assert from 'node:assert/strict';
import {runSchedule} from '../src/scheduler.js';

test('停止中・準備中のランキングは起動しない。アーカイブ停止でもランキングは独立', async () => {
  for (const config of [
    {enabled:true,ranking_enabled:false,ranking_ready:true},
    {enabled:false,ranking_enabled:true,ranking_ready:false},
    {enabled:false,ranking_enabled:true,ranking_ready:true},
    {enabled:true,ranking_enabled:true,ranking_ready:false,ranking_scheduler_probe:true},
  ]) {
    let dispatch = null;
    const result = await runSchedule({GITHUB_TOKEN:'test_key'}, async (url, options) => {
      if(url.includes('/contents/')) return Response.json({content:Buffer.from(JSON.stringify(config)).toString('base64')});
      if(url.includes('/runs?')) return Response.json({workflow_runs:[]});
      dispatch = JSON.parse(options.body);
      assert.equal(options.redirect,'manual');
      return new Response(null,{status:204});
    });
    if(config.ranking_ready && config.ranking_enabled) { assert.equal(result,'dispatched');assert.equal(dispatch.inputs.dry_run,'false'); }
    else if(config.ranking_scheduler_probe) {assert.equal(result,'probe-dispatched');assert.equal(dispatch.inputs.dry_run,'true');}
    else {assert.equal(result,'paused');assert.equal(dispatch,null);}
  }
});
test('実行中の監視を重ねて起動しない', async () => {
  const result=await runSchedule({GITHUB_TOKEN:'test_key'}, async url => url.includes('/contents/')
    ? Response.json({content:Buffer.from('{"ranking_enabled":true,"ranking_ready":true}').toString('base64')})
    : Response.json({workflow_runs:[{status:'in_progress'}]}));
  assert.equal(result,'already-running');
});
