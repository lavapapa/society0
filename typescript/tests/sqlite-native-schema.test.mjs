import test from 'node:test';import assert from 'node:assert/strict';import {mkdtempSync}from'node:fs';import{tmpdir}from'node:os';import{join}from'node:path';import{openConnection}from'../.build/sqlite-native/src/index.mjs';
const root=join(tmpdir(),'sqlite-native-');
test('真实native计数区分缓存验证、attach与Session内部metadata读取',()=>{
 for(const idle of [0,16]){
  const home=mkdtempSync(root+'schema-'),d=openConnection({path:home+'/db'}),tables=['active',...Array.from({length:idle},(_,i)=>'idle_'+i)];
  try{
   d.execSchema('BEGIN;'+tables.map(t=>`CREATE TABLE ${t}(id INTEGER PRIMARY KEY,v INTEGER);`).join('')+'COMMIT;');
   for(let turn=0;turn<3;turn++){d.startSession(tables);d.begin();d.execute('INSERT INTO active VALUES(?,?)',[BigInt(turn),BigInt(turn)]);d.commit();d.exportSegment(home+'/s'+turn);}
   const before=d.state();assert.equal(before.tableValidationCount,tables.length);assert.equal(before.schemaValidationSqlCount,tables.length*3);assert.equal(before.sessionAttachCount,tables.length*3);assert.equal(before.sessionMetadataPrepareCount,6);
   d.execSchema('CREATE TABLE added(id INTEGER PRIMARY KEY,v INTEGER)');tables.push('added');d.startSession(tables);d.begin();d.execute('INSERT INTO active VALUES(?,?)',[9n,9n]);d.commit();d.exportSegment(home+'/last');const after=d.state();assert.equal(after.tableValidationCount,before.tableValidationCount+tables.length);assert.equal(after.sessionAttachCount,before.sessionAttachCount+tables.length);
   console.log(JSON.stringify({idle,before,after}));
  }finally{d.close();}
 }
});
