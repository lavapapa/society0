import {test} from 'node:test';
import assert from 'node:assert/strict';
import {canonicalLine,canonicalJson,normalizeDecimal,rowKey,shardFile,sortKey,validateTableDecl} from '../.build/core/dist/public/index.js';
import {table as base} from './b2-support.mjs';

const decl={...base,shard:{by:'at',format:'month'}};
test('规范行按schema声明顺序、缺失补null、未知字段拒绝',()=>{
 const line=canonicalLine(decl,{note:'n',count:3,id:'a',at:'2026-11-06T00:00:00Z'});
 assert.equal(line,'{"id":"a","at":"2026-11-06T00:00:00Z","price":null,"note":"n","meta":null,"count":3}');
 assert.throws(()=>canonicalLine(decl,{id:'a',at:'x',extra:1}),/PUBLIC_FIELD_UNKNOWN/);
 assert.throws(()=>canonicalLine(decl,[]),/PUBLIC_ROW/);
});
test('浮点直写拒绝、十进制字符串规范化',()=>{
 assert.throws(()=>canonicalLine(decl,{id:'a',at:'x',count:0.1+0.2}),/PUBLIC_FLOAT/);
 assert.throws(()=>canonicalLine(decl,{id:'a',at:'x',meta:{value:0.5}}),/PUBLIC_FLOAT/);
 assert.equal(normalizeDecimal('00012.3400'),'12.3400');
 assert.equal(normalizeDecimal('-0.0'),'0');
 assert.equal(normalizeDecimal('-000.500'),'-0.500');
 assert.throws(()=>normalizeDecimal('1e3'),/PUBLIC_DECIMAL/);
 assert.throws(()=>normalizeDecimal(' 1'),/PUBLIC_DECIMAL/);
 assert.throws(()=>canonicalLine(decl,{id:'a',at:'x',price:1.5}),/PUBLIC_FLOAT/);
 assert.equal(canonicalLine(decl,{id:'a',at:'x',price:'-000.500'}),'{"id":"a","at":"x","price":"-0.500","note":null,"meta":null,"count":null}');
});
test('嵌套对象键按code point排序、孤立代理项拒绝',()=>{
 assert.deepEqual(canonicalJson({b:1,a:{d:2,c:3}}),{a:{c:3,d:2},b:1});
 assert.equal(JSON.stringify(canonicalJson({b:1,a:{d:2,c:3}})),'{"a":{"c":3,"d":2},"b":1}');
 assert.throws(()=>canonicalJson('a\uD800'),/PUBLIC_TEXT/);
 assert.throws(()=>canonicalLine(decl,{id:'a\uDC00',at:'x'}),/PUBLIC_TEXT/);
});
test('分片文件名与排序键稳定',()=>{
 assert.equal(shardFile(decl,{id:'a',at:'2026-11-06T00:00:00Z'}),'/public/market/prices/2026-11.jsonl');
 assert.equal(shardFile({...decl,shard:{by:'id',format:'value'}},{id:'锂/价'}),'/public/market/prices/%E9%94%82%2F%E4%BB%B7.jsonl');
 assert.equal(shardFile({...decl,shard:{by:'',format:'none'}},{id:'a'}),'/public/market/prices/prices.jsonl');
 assert.equal(rowKey(decl,{id:'a'}),'["a"]');
 const early=sortKey(decl,{id:'a',at:'2026-11-01'}),late=sortKey(decl,{id:'a',at:'2026-11-02'});
 assert.ok(early<late);
 const desc=sortKey({...decl,sort:[{field:'at',order:'desc'}]},{id:'a',at:'2026-11-01'});
 assert.ok(desc>sortKey({...decl,sort:[{field:'at',order:'desc'}]},{id:'a',at:'2026-11-02'}));
 assert.throws(()=>validateTableDecl({...decl,key:['missing']}),/PUBLIC_FIELD_UNKNOWN/);
});
