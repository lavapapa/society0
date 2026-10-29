import { parentPort, workerData } from 'node:worker_threads';
import { DatabaseSync } from 'node:sqlite';
const database=new DatabaseSync(':memory:');
const gate=new Int32Array(workerData);
database.function('wait_release',()=>{
  parentPort.postMessage({type:'native-running'});
  Atomics.wait(gate,0,0);
  return 1;
});
parentPort.on('message',message=>{
  if(message==='query') {
    const result=database.prepare('WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<100000) SELECT sum(x)+wait_release() AS total FROM n').get();
    parentPort.postMessage({type:'result',total:result.total});
  } else if(message==='close') {
    database.close();parentPort.close();
  }
});
