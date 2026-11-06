import {test} from 'node:test';
import assert from 'node:assert/strict';
import {page,cursor,jsonBytes} from '../.build/core/dist/information/page.js';

const items=Array.from({length:60},(_,index)=>({
 path:`/public/market/prices/f${String(index).padStart(3,'0')}.jsonl`,
 objectId:`public:file:${index}`,
 kind:'file',
 versions:[{owner:'public',version:String(index)},{owner:'moment',version:'9'},{owner:'audience',version:'all'}],
 rawBytes:String(index*7),
 provenance:{etag:`/public/...\u0000${index}\u0000all`,rows:index,table:'/public/market/prices',note:'中文\"引号\\反斜杠\n换行'}
}));
const id={fixture:'b4-page'};
const key=node=>node.path;

function legacyBytes(value){return Buffer.byteLength(JSON.stringify(value))}
function legacyPage(list,total,revision,request,more=false){
 const selected=list.slice(0,request.maxItems);
 while(true){
  const hasMore=more||selected.length<list.length;
  const value={items:[...selected],total,revision,...(hasMore&&selected.length?{next:cursor(id,key(selected[selected.length-1]))}:{}),responseBytes:0};
  let n=legacyBytes(value);
  while(n!==value.responseBytes){value.responseBytes=n;n=legacyBytes(value)}
  if(n<=request.maxResponseBytes)return value;
  if(selected.length<=1)throw Object.assign(new Error('PAGE_RESPONSE_BUDGET'),{code:'PAGE_RESPONSE_BUDGET'});
  selected.pop();
 }
}

test('jsonBytes与JSON.stringify的UTF-8长度一致',()=>{
 for(const item of items)assert.equal(jsonBytes(item),legacyBytes(item));
 assert.equal(jsonBytes({a:[1,undefined,3],b:'\ud800',c:null,d:true,e:false}),legacyBytes({a:[1,undefined,3],b:'\ud800',c:null,d:true,e:false}));
});

test('增量分页的截断点、游标与responseBytes与旧实现一致',()=>{
 for(const maxResponseBytes of [700,900,1200,2000,4000,12000]){
  const request={maxItems:100,maxResponseBytes};
  const expected=legacyPage(items,'60','9',request);
  const actual=page(items,'60','9',request,id,key);
  assert.deepEqual(actual.items,expected.items,`maxResponseBytes=${maxResponseBytes}`);
  assert.equal(actual.next,expected.next);
  assert.equal(actual.responseBytes,expected.responseBytes);
  assert.equal(Buffer.byteLength(JSON.stringify(actual)),actual.responseBytes);
 }
 const paged=page(items,'60','9',{maxItems:6,maxResponseBytes:1200},id,key);
 const expected=legacyPage(items,'60','9',{maxItems:6,maxResponseBytes:1200});
 assert.deepEqual(paged.items,expected.items);
 assert.equal(paged.next,expected.next);
});
