import test from 'node:test';
import assert from 'node:assert/strict';
import {nodes, edges, visibleGraph} from './model.js';

test('所有关系具有真实端点和唯一标识', () => {
  const ids = new Set(nodes.map(n => n.id));
  assert.equal(new Set(edges.map(e=>e.id)).size, edges.length);
  for (const e of edges) { assert.ok(ids.has(e.source)); assert.ok(ids.has(e.target)); }
});
test('初始配置只有关注关系；帖子按发布时间出现', () => {
  const g = visibleGraph({tick:0, layers:['follow','publish','reply','share']});
  assert.ok(g.edges.every(e=>e.type==='follow'));
  assert.ok(g.nodes.every(n=>n.kind==='agent'));
});
test('关系过滤、时间窗口与对象邻域保持一致', () => {
  const g = visibleGraph({tick:3,layers:['reply'],scope:'moment'});
  assert.ok(g.edges.length > 0);
  assert.ok(g.edges.every(e=>e.type==='reply'&&e.tick===3));
  const h = visibleGraph({tick:4,layers:['follow','publish','reply','share'],ego:'a1'});
  assert.ok(h.edges.every(e=>e.source==='a1'||e.target==='a1'));
  assert.ok(h.nodes.some(n=>n.id==='a1'));
});
