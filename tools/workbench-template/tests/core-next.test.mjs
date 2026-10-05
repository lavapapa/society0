import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFileSync} from 'node:fs';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';

test('新版正式运行结果实际渲染表格与指标曲线', {skip:!process.env.CORE_NEXT_WORKBENCH_PAYLOAD}, async()=>{
  const payload=JSON.parse(readFileSync(process.env.CORE_NEXT_WORKBENCH_PAYLOAD,'utf8'));
  const server=await createServer({configFile:false,plugins:[(await import('@vitejs/plugin-react')).default()],
    optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true},appType:'custom'});
  try {
    const {ViewTabs}=await server.ssrLoadModule('/src/views.jsx');
    const views=payload.versions[0].runs[0].snapshots.filter(s=>s.entityId==='environment')
      .flatMap(s=>s.tabs).flatMap(t=>t.modules).flatMap(m=>m.views);
    const table=views.find(v=>v.type==='table'&&v.columns.some(c=>c.key==='sent_to'));
    const trend=views.filter(v=>v.type==='timeseries').at(-1);
    assert.equal(table.rows.length,8);
    assert.equal(trend.rows.length,2);
    const render=view=>renderToStaticMarkup(React.createElement(ViewTabs,{views:[view],context:{}}));
    assert.match(render(table),/role="table"/);
    assert.match(render(table),/sent_to/);
    assert.match(render(trend),/data-chart/);
    assert.match(render(trend),/2 · 2/);
  } finally {await server.close();}
});
