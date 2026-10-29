import {parentPort,workerData} from 'node:worker_threads';
import {openConnection} from '../packages/sqlite-native/src/index.mjs';
if(parentPort){
 const connection=openConnection(workerData);
 parentPort.postMessage({event:'ready'});
 parentPort.on('message',({id,method,args=[]})=>{
  parentPort.postMessage({event:'started',id});
  try{const value=connection[method](...args);parentPort.postMessage({id,value});}
  catch(error){parentPort.postMessage({id,error:{message:error.message,code:error.code,sqliteCode:error.sqliteCode},state:connection.state()});}
  if(method==='close')parentPort.close();
 });
}
