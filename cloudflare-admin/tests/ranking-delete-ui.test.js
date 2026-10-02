import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

function page(fetcher, confirm = true) {
  const nodes = new Map();
  const node = id => {
    if (!nodes.has(id)) nodes.set(id,{textContent:'',innerHTML:'',hidden:false,
      querySelectorAll:()=>[],setAttribute:()=>{},addEventListener:()=>{}});
    return nodes.get(id);
  };
  const context = vm.createContext({Response,AbortSignal,URLSearchParams,fetch:fetcher,
    document:{getElementById:node,addEventListener:()=>{},querySelectorAll:()=>[]},
    window:{confirm:()=>confirm,addEventListener:()=>{}},localStorage:{getItem:()=>null},
    console,refreshes:0});
  const html = readFileSync(new URL('../public/index.html',import.meta.url),'utf8');
  new vm.Script(html.match(/<script>([\s\S]*?)<\/script>/)[1]).runInContext(context);
  vm.runInContext(`
    activeView = 'ranking';
    rankingChecksVersion = 'checks-1';
    rankingBlocklistVersion = 'blocks-1';
    rankingData = {rows:[
      {id:'111',name:'A<test>',profileUrl:'https://mixch.tv/u/111',days:2,rankDays:{1:2,2:0,3:0},placement:1},
      {id:'222',name:'B',profileUrl:'https://mixch.tv/u/222',days:1,rankDays:{1:1,2:0,3:0},placement:2}
    ],ranks:[1],minMomentum:0,meta:{periodObservedDays:2,totalObservedDays:2}};
    loadRanking = () => { refreshes++; };
    renderRanking();
  `,context);
  return {context,node,run:code=>vm.runInContext(code,context)};
}

test('削除はチェックとは別に表示され、確認をキャンセルすれば保存しない',async()=>{
  let calls = 0;
  const p = page(()=>{calls++;},false);
  assert.match(p.node('rankingList').innerHTML,/data-streamer-check="111"/);
  assert.match(p.node('rankingList').innerHTML,/data-streamer-delete="111"/);
  assert.match(p.node('rankingList').innerHTML,/A&lt;test&gt;/);
  await p.run("deleteRankingStreamer('111')");
  assert.equal(calls,0);
  assert.match(p.node('rankingList').innerHTML,/data-streamer-id="111"/);
});

test('同時削除の競合を再試行し、他のブロックを残して対象を消す',async()=>{
  const calls=[];
  const p = page(async(_url,options)=>{
    const body=options.body && JSON.parse(options.body);
    calls.push({method:options.method,body});
    if (calls.length===1) return Response.json({error:'conflict'},{status:409});
    if (options.method==='GET') return Response.json({blocked:{333:true},version:'blocks-2'});
    assert.equal(body.version,'blocks-2');
    return Response.json({blocked:{111:true,333:true},version:'blocks-3'});
  });
  await p.run("deleteRankingStreamer('111')");
  assert.deepEqual(calls.map(call=>call.method),['POST','GET','POST']);
  assert.equal(p.run('rankingBlocked[333]'),true);
  assert.doesNotMatch(p.node('rankingList').innerHTML,/data-streamer-id="111"/);
  assert.match(p.node('rankingList').innerHTML,/data-streamer-id="222"/);
  assert.equal(p.run('refreshes'),1);
});

test('保存後の通信切断は再読込みで確認し、未保存の場合はエラーと対象を残す',async()=>{
  for (const saved of [true,false]) {
    const p = page(async(_url,options)=>{
      if (options.method==='POST') throw new TypeError('connection lost');
      return Response.json({blocked:saved?{111:true}:{},version:'blocks-2'});
    });
    await p.run("deleteRankingStreamer('111')");
    assert.equal(p.node('rankingList').innerHTML.includes('data-streamer-id="111"'),!saved);
    assert.equal(p.node('rankingBlockStatus').className.includes('error'),!saved);
  }
});
