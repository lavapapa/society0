import {fixture,prepare,advance} from './pr05-support.mjs';
const [directory,phase]=process.argv.slice(2);
const f=await fixture({directory,onPhase:async(current,operationId)=>{if(current===phase&&operationId==='killed'){await new Promise((resolve,reject)=>process.send({phase:current},error=>error?reject(error):resolve()));await new Promise(()=>{})}}});
const root=await prepare(f);await advance(f,1);await f.lastTask.settled;const lease=await f.fence.seal();await f.history.publish(lease,{operationId:'killed',id:'killed',parentId:root.id,expectedRevision:lease.sealedRevision,requiredOperations:['step-1']});await new Promise(resolve=>process.send({phase:'unreached'},resolve));process.exit(0);
