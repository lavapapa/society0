export function waitForPhase(phase,operation,label){
 return Promise.race([phase,new Promise((_,reject)=>setTimeout(()=>reject(Error(`${label} timeout`)),10000))]);
}
