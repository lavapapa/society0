import {test} from 'node:test';
import assert from 'node:assert/strict';
import {Context} from '../.build/host/dist/abi.js';
import {DiagnosticSink} from '../.build/host/dist/diagnostics/index.js';
import {OwnedResources} from '../.build/host/dist/resources/index.js';
import {CompositionStrategies} from '../.build/host/dist/composition-strategies/index.js';
import {Slots,slotKey} from '../.build/host/dist/slots/index.js';
import defaults,{composeMiddleware} from '../.build/host/dist/slot-strategies/index.js';
async function root(){const ctx=new Context();await ctx.plugin(DiagnosticSink);await ctx.plugin(OwnedResources);await ctx.plugin(CompositionStrategies);await ctx.plugin(Slots);return ctx;}
const key=slotKey('author/filter',1);
const spec={key,strategy:'third-party',required:true,validate:value=>{if(typeof value!=='function')throw Error('filter must be function');return value}};
test('PR03-T02 external named strategy, declaration-late author contribution and real caller ownership',async()=>{
 const ctx=await root();let calls=0;let child;
 const contributor=await ctx.plugin({inject:['slots'],apply(owner){owner.slots.inject(key,scope=>{child=scope.fiber;scope.slots.contribute(key,{id:'filter',value:text=>text.toUpperCase()});calls++})}});
 const declared=await ctx.plugin({inject:['slots'],apply(owner){owner.slots.declare(spec)}});
 await assert.rejects(ctx.slots.acquire(key,{generation:'1'}),/STRATEGY_PENDING/);
 await ctx.plugin({inject:['compositionStrategies'],apply(owner){owner.compositionStrategies.register({name:'third-party',select:items=>items.map(item=>item.id)})}});
 await new Promise(setImmediate);
 const lease=await ctx.slots.acquire(key,{generation:'1'});assert.equal(lease.items[0].value('hello'),'HELLO');assert.equal(calls,1);assert.notEqual(child,contributor);assert.equal(child.parent.fiber,contributor.ctx.fiber);
 await lease.release();await contributor.dispose();
 await assert.rejects(ctx.slots.acquire(key,{generation:'1'}),/REQUIRED/);
 await declared.dispose();await ctx.fiber.dispose();assert.equal(ctx.resources,undefined);
});
test('PR03-T03 retiring contribution preserves old lease, successor and idempotent disposer',async()=>{
 const ctx=await root();await ctx.plugin(defaults);let remove;
 await ctx.plugin({inject:['slots'],apply(owner){owner.slots.declare({...spec,strategy:'single'})}});
 const old=await ctx.plugin({inject:['slots'],apply(owner){remove=owner.slots.contribute(key,{id:'filter',value:()=>1})}});
 const lease=await ctx.slots.acquire(key,{generation:'old'});let stopped=false;const closing=Promise.resolve(remove()).then(()=>{stopped=true});
 await new Promise(setImmediate);assert.equal(stopped,false);
 await ctx.plugin({inject:['slots'],apply(owner){owner.slots.contribute(key,{id:'filter',value:()=>2})}});
 const next=await ctx.slots.acquire(key,{generation:'next'});assert.equal(lease.items[0].value(),1);assert.equal(next.items[0].value(),2);assert(Object.isFrozen(lease.items));
 await lease.release();await closing;await remove();assert.equal(next.items[0].value(),2);await next.release();await old.dispose();await ctx.fiber.dispose();
});
test('version conflicts, ordering cycles and double next identify the contribution',async()=>{
 const ctx=await root();await ctx.plugin(defaults);await ctx.plugin({inject:['slots'],apply(owner){owner.slots.declare({...spec,strategy:'ordered'});owner.slots.contribute(key,{id:'a',value:()=>1,before:['b']});owner.slots.contribute(key,{id:'b',value:()=>2,before:['a']})}});
 await assert.rejects(ctx.slots.acquire(key,{generation:'1'}),/a.*b|b.*a/);
 assert.throws(()=>ctx.slots.contribute(slotKey('author/filter',2),{id:'wrong',value:()=>0}),/VERSION/);
 const middleware=composeMiddleware([{name:'twice',async execute(input,next){await next(input);return next(input)}}],async x=>x);
 await assert.rejects(middleware(1,new AbortController().signal),/twice.*next/);await ctx.fiber.dispose();
});

test('pending slot injector version is checked before declaration publication',async()=>{
 const ctx=await root();let close;await ctx.plugin({inject:['slots'],apply(owner){close=owner.slots.inject(slotKey('author/version',2),()=>{})}});
 assert.throws(()=>ctx.slots.declare({key:slotKey('author/version',1),strategy:'single',required:false,validate:value=>value}),/VERSION/);
 assert.equal(ctx.slots.describe().length,0);await close();await ctx.fiber.dispose();
});
