import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdir, writeFile, readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { spawnSync, spawn } from 'node:child_process';
test('installed society0 executable runs boot and closes its resources', async () => {
  const directory=resolve('node_modules/@fixtures/cli-probe');
  await mkdir(directory,{recursive:true});
  await writeFile(resolve(directory,'package.json'),JSON.stringify({name:'@fixtures/cli-probe',version:'1.0.0',type:'module',exports:{'.':'./index.js'},peerDependencies:{'@society0/host':'^0.1.0'}}));
  await writeFile(resolve(directory,'index.js'),`import {appendFileSync} from 'node:fs'; export function apply(ctx, config){appendFileSync(config.marker,'applied\\n');ctx.effect(()=>()=>{appendFileSync(config.marker,'closed\\n')})}`);
  const marker=resolve('cli-probe.txt');await writeFile(marker,'');
  await writeFile('cli-boot.json',JSON.stringify([{specifier:'@fixtures/cli-probe',config:{marker}}]));
  const executable=resolve('node_modules/.bin/society0');
  const result=spawnSync(executable,['--boot','cli-boot.json'],{encoding:'utf8',timeout:5000});
  assert.equal(result.status,0,result.stderr);
  assert.equal(await readFile(marker,'utf8'),'applied\nclosed\n');
  const usage=spawnSync(executable,[],{encoding:'utf8',timeout:5000});
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
  const result=spawnSync(executable,['--boot',boot],{encoding:'utf8',timeout:5000});
  assert.equal(result.error,undefined);
  assert.equal(result.status,13,result.stderr);
  assert.match(result.stderr,/unsettled top-level await/i);
  assert.equal(await readFile(marker,'utf8'),'started\n');
});
test('idle shutdown awaits asynchronous cleanup to completion', async () => {
  const {boot,marker}=await bootFixture('cli-async',`import {appendFileSync} from 'node:fs';import {setTimeout} from 'node:timers/promises';export function apply(ctx,{marker}) {ctx.effect(()=>async()=>{appendFileSync(marker,'started\\n');await setTimeout(25);appendFileSync(marker,'closed\\n')})}`);
  const result=spawnSync(executable,['--boot',boot],{encoding:'utf8',timeout:5000});
  assert.equal(result.status,0,result.stderr);
  assert.equal(await readFile(marker,'utf8'),'started\nclosed\n');
});
test('SIGTERM awaits asynchronous cleanup and stops owned timer', {timeout:5000}, async () => {
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
  const result=spawnSync(executable,['--boot','cli-invalid.json'],{encoding:'utf8',timeout:5000});
  assert.equal(result.status,1);assert.match(result.stderr,/PACKAGE_MISSING/);
});
