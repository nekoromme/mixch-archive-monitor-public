// 外部接続をせず、管理画面と保存処理の受け渡しを検証します。
const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
const path = require('path');
const root = path.join(__dirname, '..');
const context = vm.createContext({
  PropertiesService: {getScriptProperties: () => ({getProperty: () => ''})}
});
vm.runInContext(fs.readFileSync(path.join(root, 'Code.gs'), 'utf8'), context);
let data = [{id:'1', name:'test', extra:'keep'}];
context.fetchJsonFile_ = () => ({json: structuredClone(data), sha:'test-sha'});
context.saveJsonFile_ = next => {data = next; return {ok:true};};
const target = enabled => ({index:0, id:'1', name:'test', archiveEnabled:enabled});
assert.equal(context.normalizeWatchlist_(data).items[0].archiveEnabled, true);
context.updateStreamer(target(true), {id:'1', name:'test', archive_enabled:false});
assert.equal(data[0].archive_enabled, false);
assert.equal(data[0].extra, 'keep');
assert.throws(() => context.updateStreamer(target(true), {id:'1',name:'test',archive_enabled:true}));
assert.throws(() => context.updateStreamer(target(false), {id:'1',name:'test',archive_enabled:'false'}));
context.updateStreamer({index:0,id:'1',name:'test'}, {id:'1',name:'test'});
assert.equal(data[0].archive_enabled, false);
context.updateStreamer(target(false), {id:'1',name:'test',archive_enabled:true});
assert.equal(data[0].archive_enabled, true);
const html = fs.readFileSync(path.join(root,'Index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
new vm.Script(script); // HTML内のJavaScriptも構文確認する。
console.log('Admin toggle: defaults, off/on, stale edit, validation, metadata preservation OK');
