export function waitForPhase(phase,operation,label){
 return Promise.race([phase,operation.then(()=>{throw Error(`${label}: operation completed before phase`)})]);
}
