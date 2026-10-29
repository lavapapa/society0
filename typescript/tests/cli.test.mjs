import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdir, writeFile, readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { spawnSync, spawn } from 'node:child_process';
// Process startup guard includes cold filesystem reads under the shared CPU budget.
const processTimeout = 30_000;
test('installed society0 executable runs boot and closes its resources', async () => {
  const directory=resolve('node_modules/@fixtures/cli-probe');
  await mkdir(directory,{recursive:true});
  await writeFile(resolve(directory,'package.json'),JSON.stringify({name:'@fixtures/cli-probe',version:'1.0.0',type:'module',exports:{'.':'./index.js'},peerDependencies:{'@society0/host':'^0.1.0'}}));
  await writeFile(resolve(directory,'index.js'),`import {appendFileSync} from 'node:fs'; export function apply(ctx, config){appendFileSync(config.marker,'applied\\n');ctx.effect(()=>()=>{appendFileSync(config.marker,'closed\\n')})}`);
  const marker=resolve('cli-probe.txt');await writeFile(marker,'');
  await writeFile('cli-boot.json',JSON.stringify([{specifier:'@fixtures/cli-probe',config:{marker}}]));
  const executable=resolve('node_modules/.bin/society0');
  const result=spawnSync(executable,['--boot','cli-boot.json'],{encoding:'utf8',timeout:processTimeout});
  assert.equal(result.error,undefined);
  assert.equal(result.status,0,result.stderr);
  assert.equal(await readFile(marker,'utf8'),'applied\nclosed\n');
  const usage=spawnSync(executable,[],{encoding:'utf8',timeout:processTimeout});
  assert.equal(usage.status,2);assert.match(usage.stderr,/Usage: society0 --boot/);
});

async function bootFixture(name, code) {
  const directory=resolve('node_modules/@fixtures/'+name);
  await mkdir(directory,{recursive:true});
  await writeFile(resolve(directory,'package.json'),JSON.stringify({name:'@fixtures/'+name,version:'1.0.0',type:'module',exports:{'.':'./index.js'},peerDependencies:{'@society0/host':'^0.1.0'}}));
  await writeFile(resolve(directory,'index.js'),code);
  const marker=resolve(name+'.txt'); await writeFile(marker,'');
  const boot=resolve(name+'.json');
  await writeFile(boot,JSON.stringify([{specifier:'@fixtures/'+name,config:{marker}}]));
  return {boot,marker};
}
const executable=resolve('node_modules/.bin/society0');
test('unsettled cleanup with no event-loop handles exits non-successfully', async () => {
  const {boot,marker}=await bootFixture('cli-unsettled',`import {appendFileSync} from 'node:fs';export function apply(ctx,{marker}) {ctx.effect(()=>async()=>{appendFileSync(marker,'started\\n');await new Promise(()=>{});appendFileSync(marker,'closed\\n')})}`);
  const result=spawnSync(executable,['--boot',boot],{encoding:'utf8',timeout:processTimeout});
  assert.equal(result.error,undefined);
  assert.equal(result.status,13,result.stderr);
  assert.match(result.stderr,/unsettled top-level await/i);
  assert.equal(await readFile(marker,'utf8'),'started\n');
});
test('idle shutdown awaits asynchronous cleanup to completion', async () => {
  const {boot,marker}=await bootFixture('cli-async',`import {appendFileSync} from 'node:fs';import {setTimeout} from 'node:timers/promises';export function apply(ctx,{marker}) {ctx.effect(()=>async()=>{appendFileSync(marker,'started\\n');await setTimeout(25);appendFileSync(marker,'closed\\n')})}`);
  const result=spawnSync(executable,['--boot',boot],{encoding:'utf8',timeout:processTimeout});
  assert.equal(result.status,0,result.stderr);
  assert.equal(await readFile(marker,'utf8'),'started\nclosed\n');
});
test('SIGTERM awaits asynchronous cleanup and stops owned timer', {timeout:processTimeout}, async () => {
  const {boot,marker}=await bootFixture('cli-signal',`import {appendFileSync} from 'node:fs';import {setTimeout} from 'node:timers/promises';export function apply(ctx,{marker}) {ctx.effect(()=>{const timer=setInterval(()=>{},1000);return async()=>{clearInterval(timer);appendFileSync(marker,'started\\n');await setTimeout(25);appendFileSync(marker,'closed\\n')}});console.log('ready')}`);
  const child=spawn(executable,['--boot',boot],{stdio:['ignore','pipe','pipe']});
  let stderr='';child.stderr.on('data',chunk=>{stderr+=chunk});
  const exited=new Promise((resolve,reject)=>{child.once('error',reject);child.once('exit',(code,signal)=>resolve({code,signal}))});
  try {
    await new Promise((resolve,reject)=>{child.stdout.once('data',resolve);child.once('error',reject)});
    child.kill('SIGTERM');
    const result=await exited;
    assert.equal(result.code,0,stderr);assert.equal(result.signal,null);
    assert.equal(await readFile(marker,'utf8'),'started\nclosed\n');
  } finally { if(child.exitCode===null && child.signalCode===null) child.kill('SIGKILL'); }
});
test('invalid boot entry reports failure', async () => {
  await writeFile('cli-invalid.json',JSON.stringify([{specifier:'@fixtures/does-not-exist'}]));
  const result=spawnSync(executable,['--boot','cli-invalid.json'],{encoding:'utf8',timeout:processTimeout});
  assert.equal(result.status,1);assert.match(result.stderr,/PACKAGE_MISSING/);
});

test('CLI cleanup failure exits nonzero and reports concrete resource residue',async()=>{
  const {boot}=await bootFixture('cli-close-fault',`export function apply(ctx){ctx.resources.use('cli-broken-handle',async()=>{throw Error('CLI resource failure')})};export const inject=['resources'];`);
  const entries=JSON.parse(await readFile(boot,'utf8'));
  await writeFile(boot,JSON.stringify([{specifier:'@society0/host/resources'},...entries]));
  const result=spawnSync(executable,['--boot',boot],{encoding:'utf8',timeout:processTimeout});
  assert.equal(result.status,1,result.stderr);assert.match(result.stderr,/HOST_HALTED/);assert.match(result.stderr,/cli-broken-handle/);
});

async function commandFixture(name,body,resourceConfig={}) {
  const fixture=await bootFixture(name,`import {appendFileSync} from 'node:fs';export const inject=['commands','resources'];export function apply(ctx,{marker}){ctx.resources.use('command-owner',()=>{appendFileSync(marker,'closed\\n')});ctx.commands.register({name:'probe',summary:'CLI command probe',scope:'host',input:value=>value,async execute(ctx,input,invocation){${body}}})}`);
  const entries=JSON.parse(await readFile(fixture.boot,'utf8'));
  await writeFile(fixture.boot,JSON.stringify([{specifier:'@society0/host/resources',config:resourceConfig},{specifier:'@society0/host/commands'},...entries]));return fixture;
}
test('CLI generic command uses explicit boot input and drains before success',async()=>{
 const {boot,marker}=await commandFixture('cli-command',`appendFileSync(marker,'executed\\n');return {input,kind:invocation.kind,request:typeof invocation.requestId==='string',aborted:invocation.signal.aborted}`);
 const result=spawnSync(executable,['--boot',boot,'--command','probe','--input','{"value":7}'],{encoding:'utf8',timeout:processTimeout});
 assert.equal(result.status,0,result.stderr);assert.deepEqual(JSON.parse(result.stdout),{input:{value:7},kind:'host',request:true,aborted:false});assert.equal(await readFile(marker,'utf8'),'executed\nclosed\n');
});
test('CLI command failure still drains and cleanup timeout remains non-successful',async()=>{
 const failed=await commandFixture('cli-command-error',`throw Error('actual command failed')`);
 let result=spawnSync(executable,['--boot',failed.boot,'--command','probe','--input','{}'],{encoding:'utf8',timeout:processTimeout});
 assert.equal(result.status,1,result.stderr);assert.match(result.stderr,/actual command failed/);assert.equal(await readFile(failed.marker,'utf8'),'closed\n');
 const timed=await commandFixture('cli-command-timeout',`ctx.resources.use('unsettled-command-resource',()=>new Promise(()=>{}));return {ok:true}`,{closeTimeoutMs:10});
 result=spawnSync(executable,['--boot',timed.boot,'--command','probe','--input','{}'],{encoding:'utf8',timeout:processTimeout});
 assert.equal(result.status,1,result.stderr);assert.match(result.stderr,/HOST_HALTED/);assert.equal(result.stdout,'');
});

test('CLI command SIGTERM joins physical command completion before resource close',{timeout:processTimeout},async()=>{
 const {boot,marker}=await commandFixture('cli-command-signal',`const timer=setInterval(()=>{},1000);console.log('command-ready');try{await new Promise(resolve=>invocation.signal.addEventListener('abort',resolve,{once:true}))}finally{clearInterval(timer)};await new Promise(resolve=>setTimeout(resolve,25));appendFileSync(marker,'physical-done\\n');return {done:true}`);
 const child=spawn(executable,['--boot',boot,'--command','probe','--input','{}'],{stdio:['ignore','pipe','pipe']});let stderr='';child.stderr.on('data',chunk=>{stderr+=chunk});
 const exited=new Promise((resolve,reject)=>{child.once('error',reject);child.once('exit',(code,signal)=>resolve({code,signal}))});
 try{
  await new Promise((resolve,reject)=>{child.stdout.once('data',resolve);child.once('error',reject);child.once('exit',()=>reject(Error('command exited before ready: '+stderr)))});
  child.kill('SIGTERM');const result=await exited;assert.equal(result.code,143,stderr);assert.equal(result.signal,null);assert.equal(await readFile(marker,'utf8'),'physical-done\nclosed\n');
 }finally{if(child.exitCode===null&&child.signalCode===null)child.kill('SIGKILL')}
});

test('installed CLI help succeeds before reading boot',()=>{
 for(const args of [['--help'],['-h'],['--help','--boot','absent-boot.json']]){
  const result=spawnSync(executable,args,{encoding:'utf8',timeout:processTimeout});
  assert.equal(result.error,undefined);assert.equal(result.status,0,result.stderr);assert.equal(result.stderr,'');
  for(const option of ['Usage:','--boot','--command','--input','--help'])assert.ok(result.stdout.includes(option),option);
 }
});
