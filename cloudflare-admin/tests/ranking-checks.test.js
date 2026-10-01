import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createApp } from '../src/app.js';

const env = { GITHUB_TOKEN:'test_only', ADMIN_PASSWORD:'test_only_password',
  LOGIN_LIMITER:{limit:async()=>({success:true})} };
const origin = 'https://admin.example.test';

async function fixture() {
  let data = {version:1,checked:{222:true}}, version = 'checks-1', writes = 0, calls = 0;
  let conflictOnWrite = false;
  const app = createApp('', async (url, options) => {
    calls++;
    assert.ok(url.endsWith('/contents/ranking-checks.json' + (options.method === 'GET' ? '?ref=main' : '')));
    if (options.method === 'GET') return Response.json({sha:version,content:Buffer.from(JSON.stringify(data)).toString('base64')});
    const body = JSON.parse(options.body);
    assert.equal(body.sha,version);
    assert.equal(body.branch,'main');
    if (conflictOnWrite) return Response.json({}, {status:409});
    data = JSON.parse(Buffer.from(body.content,'base64').toString('utf8'));
    version = 'checks-' + (++writes + 1);
    return Response.json({content:{sha:version}});
  });
  const login = await app.fetch(new Request(origin+'/api/login',{
    method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},
    body:JSON.stringify({password:env.ADMIN_PASSWORD}),
  }),env,{});
  const cookie = login.headers.get('Set-Cookie').split(';')[0];
  const send = (body, options={}) => app.fetch(new Request(origin+'/api/ranking-checks',{
    method:options.method || (body ? 'POST':'GET'),
    headers:{...(options.auth === false ? {} : {Cookie:cookie}),Origin:origin,'Content-Type':'application/json',...options.headers},
    ...(body ? {body:JSON.stringify({version,...body})} : {}),
  }),env,{});
  return {send, get data(){return data;}, get calls(){return calls;}, get writes(){return writes;},
    corrupt(value){data=value;}, conflict(){conflictOnWrite=true;} };
}

test('チェックをIDで追加・解除し、他の配信者を保持して新しい保存版を返す',async()=>{
  const f = await fixture();
  assert.deepEqual(await (await f.send()).json(),{checked:{222:true},version:'checks-1'});
  const saved = await f.send({id:'111',checked:true});
  assert.equal(saved.status,200);
  assert.deepEqual(await saved.json(),{checked:{111:true,222:true},version:'checks-2'});
  assert.equal((await f.send({id:'111',checked:true})).status,200);
  assert.equal(f.writes,1); // 同じチェックへの再送は書き換えません。
  assert.equal((await f.send({id:'111',checked:false})).status,200);
  assert.deepEqual(f.data,{version:1,checked:{222:true}});
  assert.deepEqual((await (await f.send()).json()).checked,{222:true});
});

test('認証・送信元・入力・同時更新を検証し、古いチェックで上書きしない',async()=>{
  const f = await fixture();
  assert.equal((await f.send(null,{auth:false})).status,401);
  assert.equal((await f.send({id:'111',checked:true},{auth:false})).status,401);
  assert.equal((await f.send({id:'111',checked:true},{headers:{Origin:'https://other.test'}})).status,403);
  assert.equal(f.calls,0);
  for (const body of [{id:'__proto__',checked:true},{id:'111',checked:'true'},{id:'https://mixch.tv/u/111',checked:true},{id:'9'.repeat(31),checked:true}]) {
    assert.equal((await f.send(body)).status,400);
  }
  assert.equal((await f.send({id:'111',checked:true,version:'stale'})).status,409);
  f.conflict();
  assert.equal((await f.send({id:'111',checked:true})).status,409);
  assert.equal(f.writes,0);
  assert.deepEqual(f.data.checked,{222:true});
  assert.equal((await f.send(null,{method:'DELETE'})).status,405);
});

test('壊れたチェックは空にせずエラーにし、保存も拒否する',async()=>{
  const f = await fixture();
  for (const corrupt of [null,{version:1,checked:[]},{version:2,checked:{}},{version:1,checked:{111:false}},{version:1,checked:{abc:true}}]) {
    f.corrupt(corrupt);
    assert.equal((await f.send()).status,502);
    assert.equal((await f.send({id:'111',checked:true})).status,502);
  }
  assert.equal(f.writes,0);
});
