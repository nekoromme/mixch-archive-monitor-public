import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createApp } from '../src/app.js';

const env = { GITHUB_TOKEN:'test_only', ADMIN_PASSWORD:'test_only_password',
  LOGIN_LIMITER:{limit:async()=>({success:true})} };
const origin = 'https://admin.example.test';

async function fixture() {
  let data = {version:1,blocked:{222:true}}, version = 'blocks-1', writes = 0, calls = 0;
  let conflict = false, invalidResult = false;
  const history = {version:1,time_zone:'Asia/Tokyo',first_observed_at:null,last_observed_at:null,
    profiles:{111:{name:'A'},222:{name:'B'}},days:{'2026-10-01':{observations:1,users:{111:1,222:2}}}};
  const app = createApp('', async (url, options) => {
    calls++;
    if (url.includes('/contents/ranking-days.json?')) return Response.json(history);
    assert.ok(url.includes('/contents/ranking-blocklist.json'));
    if (options.method === 'GET') return Response.json({sha:version,content:Buffer.from(JSON.stringify(data)).toString('base64')});
    const body = JSON.parse(options.body);
    assert.equal(body.sha,version);
    assert.equal(body.branch,'main');
    if (conflict) return Response.json({}, {status:409});
    data = JSON.parse(Buffer.from(body.content,'base64').toString('utf8'));
    version = 'blocks-' + (++writes + 1);
    return Response.json(invalidResult ? {} : {content:{sha:version}});
  });
  const login = await app.fetch(new Request(origin+'/api/login',{
    method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},
    body:JSON.stringify({password:env.ADMIN_PASSWORD}),
  }),env,{});
  const cookie = login.headers.get('Set-Cookie').split(';')[0];
  const send = (body, options={}) => app.fetch(new Request(origin+(options.path || '/api/ranking-blocklist'),{
    method:options.method || (body ? 'POST':'GET'),
    headers:{...(options.auth === false ? {} : {Cookie:cookie}),Origin:origin,'Content-Type':'application/json',...options.headers},
    ...(body ? {body:JSON.stringify({version,...body})} : {}),
  }),env,{});
  return {send, get data(){return data;}, get calls(){return calls;}, get writes(){return writes;},
    corrupt(value){data=value;}, conflict(){conflict=true;}, invalidResult(){invalidResult=true;} };
}

test('既存リストを保って永続追加し、再送と画面再読込みでも復活しない',async()=>{
  const f = await fixture();
  assert.deepEqual((await (await f.send(null,{path:'/api/ranking-days?mode=all'})).json()).rows.map(row=>row.id),['111']);
  const saved = await f.send({id:'111'});
  assert.equal(saved.status,200);
  assert.deepEqual(await saved.json(),{blocked:{111:true,222:true},version:'blocks-2'});
  assert.equal((await f.send({id:'111',version:'old'})).status,200);
  assert.equal(f.writes,1);
  assert.deepEqual((await (await f.send()).json()).blocked,{111:true,222:true});
  assert.deepEqual((await (await f.send(null,{path:'/api/ranking-days?mode=all'})).json()).rows,[]);
});

test('認証・送信元・ID・同時更新を検証して他の削除対象を上書きしない',async()=>{
  const f = await fixture();
  assert.equal((await f.send(null,{auth:false})).status,401);
  assert.equal((await f.send({id:'111'},{auth:false})).status,401);
  assert.equal((await f.send({id:'111'},{headers:{Origin:'https://other.test'}})).status,403);
  assert.equal(f.calls,0);
  for (const id of ['__proto__','https://mixch.tv/u/111','9'.repeat(31),111]) assert.equal((await f.send({id})).status,400);
  assert.equal((await f.send({id:'111',version:'old'})).status,409);
  f.conflict();
  assert.equal((await f.send({id:'111'})).status,409);
  assert.equal((await f.send(null,{method:'DELETE'})).status,405);
  assert.equal(f.writes,0);
  assert.deepEqual(f.data.blocked,{222:true});
});

test('破損時は空リストにせず、ランキング表示と削除の保存を止める',async()=>{
  const f = await fixture();
  for (const value of [null,{version:2,blocked:{}},{version:1,blocked:[]},{version:1,blocked:{111:false}},{version:1,blocked:{abc:true}}]) {
    f.corrupt(value);
    assert.equal((await f.send()).status,502);
    assert.equal((await f.send({id:'111'})).status,502);
    assert.equal((await f.send(null,{path:'/api/ranking-days?mode=all'})).status,502);
  }
  assert.equal(f.writes,0);
});

test('保存後に応答が壊れても、再読込みでブロック済みを確認できる',async()=>{
  const f = await fixture();
  f.invalidResult();
  assert.equal((await f.send({id:'111'})).status,502);
  assert.equal((await (await f.send()).json()).blocked['111'],true);
  assert.equal(f.writes,1);
});
