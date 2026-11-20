import test from 'node:test';
import assert from 'node:assert/strict';
import {createHost} from '../.build/host/dist/index.js';
import {hostBoot} from '../.build/cli/dist/platform.js';

// tasks.concurrency 是每个插件 owner 的物理任务并发：model-pi 的全部模型请求共用一个 owner，默认 4 会把面板与 CEO 的并发一起压到 4。
async function peak(env){
  const host=await createHost({baseURL:new URL('../.build/cli/dist/platform.js',import.meta.url).href,boot:hostBoot(undefined,env)});
  try {
    let scope;await host.ctx.plugin({inject:['tasks'],apply(ctx){scope=ctx.tasks.bind()}});
    const gate=Promise.withResolvers();
    const works=Array.from({length:16},(_,i)=>scope.run('probe.'+i,()=>gate.promise));
    await new Promise(resolve=>setImmediate(resolve));
    const active=host.ctx.tasks.snapshot().activeCount;
    gate.resolve();await Promise.all(works);
    return active;
  } finally {await host.close()}
}

test('CLI host：SOCIETY0_HOST_CONCURRENCY 设定每个 owner 的物理任务并发，缺省为 4',async()=>{
  assert.equal(await peak({}),4);
  assert.equal(await peak({SOCIETY0_HOST_CONCURRENCY:'16'}),16);
});

test('CLI host：SOCIETY0_HOST_CONCURRENCY 非正整数直接报错',()=>{
  for(const raw of ['0','-1','2.5','abc'])assert.throws(()=>hostBoot(undefined,{SOCIETY0_HOST_CONCURRENCY:raw}),error=>error.code==='HOST_CONCURRENCY_INVALID');
});
