import {Worker} from 'node:worker_threads';
// 测试进程使用 Node 公开 API；按 operationId 精确选择一个 SQLite Worker。
export function installWorkerFault({phase,operationId,record=()=>{}}){
 if(!['before-begin','after-sql-dispatch','after-commit-reply'].includes(phase)||!operationId)throw Error('FAULT_CONFIGURATION');
 const postDescriptor=Object.getOwnPropertyDescriptor(Worker.prototype,'postMessage'),emitDescriptor=Object.getOwnPropertyDescriptor(Worker.prototype,'emit');const post=Worker.prototype.postMessage,emit=Worker.prototype.emit;let target,threadId,commitId,timer,fired=false;const events=[];
 const event=(kind,extra={})=>{const value={kind,phase,operationId,pid:process.pid,threadId,time:performance.now(),...extra};events.push(value);record(value)};
 const stop=()=>{if(fired)return;fired=true;event('terminate-requested');void target.terminate()};
 function send(message,...rest){
  if(message?.op==='begin'&&message.args?.request?.operationId===operationId){target=this;threadId=this.threadId;event('begin-observed');this.once('exit',code=>event('worker-exit',{code}));if(phase==='before-begin'){stop();return}}
  if(this===target&&message?.op==='commit')commitId=message.id;
  const result=post.call(this,message,...rest);
  if(this===target&&phase==='after-sql-dispatch'&&message?.op==='query'){event('sql-dispatched');timer=setTimeout(stop,20)}
  return result;
 }
 function receive(name,...args){const message=args[0];if(this===target&&phase==='after-commit-reply'&&name==='message'&&message?.id===commitId&&!message.error&&commitId!==undefined){event('commit-reply-observed',{receipt:message.value});stop();return false}return emit.call(this,name,...args)}
 Worker.prototype.postMessage=send;Worker.prototype.emit=receive;
 return {events,restore(){clearTimeout(timer);Object.defineProperty(Worker.prototype,'postMessage',postDescriptor);if(emitDescriptor)Object.defineProperty(Worker.prototype,'emit',emitDescriptor);else delete Worker.prototype.emit}};
}
