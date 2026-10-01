import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createApp } from '../src/app.js';
import { emptyHistory, parseFilters, summarize } from '../src/ranking.js';

const history = { ...emptyHistory(), first_observed_at:'2025-12-31T00:00:00Z', last_observed_at:'2026-10-02T01:00:00Z',
  profiles:{111:{name:'配信者A'},222:{name:'配信者B'},333:{name:'同点の配信者'}},
  days:{
    '2025-12-31':{observations:1,users:{111:1}},
    '2026-09-30':{observations:2,users:{111:2}},
    '2026-10-01':{observations:3,users:{111:5,222:2,333:4}},
    '2026-10-02':{observations:2,users:{111:4,222:1}},
  }};
const params = value => parseFilters(new URLSearchParams(value),new Date('2026-10-01T12:00:00Z'));

test('月・年・全期間を区別し、同じ日の1位と3位を二重計上しない', () => {
  const october = summarize(history,params({mode:'month',year:'2026',month:'10',ranks:'1,3'}));
  assert.equal(october.rows[0].days,2);
  assert.deepEqual(october.rows[0].rankDays,{1:1,2:0,3:2});
  assert.equal(october.rows[0].profileUrl,'https://mixch.tv/u/111');
  assert.equal(october.rows.find(row=>row.id==='222').days,1);
  assert.deepEqual(october.rows.map(row=>row.placement),[1,2,2]);
  assert.equal(summarize(history,params({mode:'year',year:'2026'})).rows[0].days,3);
  assert.equal(summarize(history,params({mode:'all'})).rows[0].days,4);
  assert.equal(summarize(history,params({mode:'month',year:'2026',month:'09'})).rows[0].days,1);
  assert.equal(summarize(history,params({mode:'month',year:'2026',month:'10',ranks:'1'})).rows.find(row=>row.id==='111').days,1);
});

test('日本時間で初期期間を選び、全解除・記録なし・不正入力を区別する', () => {
  assert.equal(parseFilters(new URLSearchParams(),new Date('2026-09-30T15:00:00Z')).month,'10');
  assert.equal(summarize(history,params({ranks:''})).rows.length,0);
  assert.equal(summarize(emptyHistory(),params({})).meta.totalObservedDays,0);
  assert.throws(()=>params({ranks:'1,4'}));
  assert.throws(()=>params({month:'13'}));
  assert.throws(()=>summarize({...history,days:{'2026-02-30':{observations:1,users:{111:1}}}},params({})));
  assert.throws(()=>summarize({...history,days:{'2026-10-01':{observations:1,users:{111:8}}}},params({})));
});

const momentumHistory = {...history,days:{
  ...history.days,
  '2026-10-01':{...history.days['2026-10-01'],momentum_complete:true,
    momentum:{111:{1:149,3:250},222:{2:150},333:{3:0}}},
  '2026-10-02':{...history.days['2026-10-02'],momentum_complete:true,
    momentum:{111:{3:150},222:{1:151}}},
}};

test('選択順位にいた時の勢いで下限を判定し、境界値と同じ日の重複を扱う',()=>{
  const counted = summarize(momentumHistory,params({mode:'month',year:'2026',month:'10',ranks:'1,3',minMomentum:'150'}));
  assert.deepEqual(counted.rows.map(row=>[row.id,row.days]),[['111',2],['222',1]]);
  assert.deepEqual(counted.rows[0].rankDays,{1:0,2:0,3:2});
  assert.equal(counted.meta.momentumIncompleteDays,0);
  const firstOnly = summarize(momentumHistory,params({ranks:'1',minMomentum:'150'}));
  assert.deepEqual(firstOnly.rows.map(row=>row.id),['222']); // 3位で250でも1位149なら数えません。
  assert.deepEqual(summarize(momentumHistory,params({ranks:'2',minMomentum:'150'})).rows.map(row=>row.id),['222']);
  assert.equal(summarize(momentumHistory,params({ranks:'2',minMomentum:'151'})).rows.length,0);
  const multiple = {...momentumHistory,days:{'2026-10-01':{...momentumHistory.days['2026-10-01'],momentum:{111:{1:150,3:250},222:{2:150},333:{3:0}}}}};
  const oneDay = summarize(multiple,params({ranks:'1,3',minMomentum:'150'})).rows[0];
  assert.equal(oneDay.days,1);
  assert.deepEqual(oneDay.rankDays,{1:1,2:0,3:1});
  assert.equal(summarize(momentumHistory,params({minMomentum:'251'})).rows.length,0);
  assert.equal(summarize(momentumHistory,params({ranks:'3',minMomentum:'0'})).rows.find(row=>row.id==='333').days,1);
});

test('勢い未記録を高い勢いとして扱わず、部分記録の警告を残す',()=>{
  assert.deepEqual(summarize(history,params({minMomentum:'0'})).rows,summarize(history,params({})).rows);
  const legacy = summarize(history,params({mode:'all',minMomentum:'150'}));
  assert.equal(legacy.rows.length,0);
  assert.equal(legacy.meta.momentumIncompleteDays,4);
  const partial = {...momentumHistory,days:{'2026-10-01':{...momentumHistory.days['2026-10-01'],momentum_complete:false}}};
  assert.equal(summarize(partial,params({minMomentum:'150'})).meta.momentumIncompleteDays,1);
  assert.equal(summarize(partial,params({ranks:'',minMomentum:'150'})).meta.momentumIncompleteDays,0);
  assert.equal(summarize(momentumHistory,params({mode:'year',year:'2026',minMomentum:'150'})).rows[0].days,2);
});

test('下限と保存された勢いを検証し、不正データは除外ではなくエラーにする',()=>{
  assert.equal(params({}).minMomentum,0);
  for(const value of ['-1','1.5','1e2','','NaN','Infinity','9007199254740992']) assert.throws(()=>params({minMomentum:value}));
  for(const momentum of [null,[],{999:{1:150}},{111:{2:150}},{111:{1:-1}},{111:{1:true}},{111:{1:1.5}}]) {
    const broken={...history,days:{'2026-10-01':{...history.days['2026-10-01'],momentum}}};
    assert.throws(()=>summarize(broken,params({minMomentum:'150'})));
  }
});

const env={GITHUB_TOKEN:'test_only',ADMIN_PASSWORD:'test_only_password',LOGIN_LIMITER:{limit:async()=>({success:true})}};
const origin='https://admin.example.test';
async function login(app) {
  const response=await app.fetch(new Request(origin+'/api/login',{
    method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify({password:env.ADMIN_PASSWORD}),
  }),env,{});
  return response.headers.get('Set-Cookie').split(';')[0];
}

test('ランキングAPIは認証必須、保存ブランチを読み、不正データを空のランキングにしない',async()=>{
  let upstream=history, status=200, calls=0;
  const app=createApp('',async(url,options)=>{
    calls++;
    assert.ok(url.endsWith('/contents/ranking-days.json?ref=ranking-state'));
    assert.equal(options.headers.Accept,'application/vnd.github.raw+json');
    assert.equal(options.method,'GET');
    return Response.json(upstream,{status});
  });
  const cookie=await login(app);
  const get=(auth=true)=>app.fetch(new Request(origin+'/api/ranking-days?mode=all',{
    headers:auth?{Cookie:cookie}:{},
  }),env,{});
  assert.equal((await get(false)).status,401);
  assert.equal(calls,0);
  const response=await get();
  assert.equal(response.status,200);
  assert.equal((await response.json()).rows[0].days,4);
  upstream={broken:true};
  assert.equal((await get()).status,502);
  status=404;
  assert.equal((await (await get()).json()).meta.totalObservedDays,0);
  status=403;
  assert.equal((await get()).status,502);
  status=200;upstream=momentumHistory;
  const filtered=await app.fetch(new Request(origin+'/api/ranking-days?mode=month&year=2026&month=10&ranks=1&minMomentum=150',{headers:{Cookie:cookie}}),env,{});
  assert.deepEqual((await filtered.json()).rows.map(row=>row.id),['222']);
  assert.equal((await app.fetch(new Request(origin+'/api/ranking-days?minMomentum=-1',{headers:{Cookie:cookie}}),env,{})).status,400);
});

test('順位記録の切り替えは既存のアーカイブと通知の設定を変えない',async()=>{
  let settings={enabled:false,ranking_enabled:false,ranking_ready:true,ranking_recording_enabled:true};
  const app=createApp('',async(_url,options)=>{
    if(options.method==='GET') return Response.json({sha:'v1',content:Buffer.from(JSON.stringify(settings)).toString('base64')});
    settings=JSON.parse(Buffer.from(JSON.parse(options.body).content,'base64').toString());
    return Response.json({});
  });
  const cookie=await login(app);
  const response=await app.fetch(new Request(origin+'/api/monitoring',{
    method:'POST',headers:{Cookie:cookie,Origin:origin,'Content-Type':'application/json'},
    body:JSON.stringify({kind:'recording',enabled:false,version:'v1'}),
  }),env,{});
  assert.equal(response.status,200);
  assert.equal(settings.enabled,false);
  assert.equal(settings.ranking_enabled,false);
  assert.equal(settings.ranking_recording_enabled,false);
});
