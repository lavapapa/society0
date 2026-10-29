import type {Context} from '@society0/host';
import type {slotKey} from '@society0/host/slots';
import type {Lease} from '../contracts/common.js';
import type {ResolvedModelSelection} from '../contracts/inference.js';
export function registerSingle<T>(ctx:Context,key:ReturnType<typeof slotKey<T>>,id:string,value:T){const undeclare=ctx.slots.declare({key,strategy:'single',required:true,validate:value=>value as T});let withdraw:()=>void|Promise<void>;try{withdraw=ctx.slots.contribute(key,{id,value})}catch(error){void undeclare();throw error}return ctx.effect(()=>async()=>{await Promise.all([withdraw(),undeclare()])})}
export function retainModelSelection(selected:Lease<ResolvedModelSelection>,provider:{close():Promise<void>},credentials:{release():Promise<void>},finished:()=>void):Lease<ResolvedModelSelection>{let closing:Promise<void>|undefined;return {value:selected.value,release:()=>closing??=(async()=>{const errors:unknown[]=[];for(const release of [()=>selected.release(),()=>provider.close(),()=>credentials.release()])try{await release()}catch(error){errors.push(error)}finished();if(errors.length)throw errors[0]})()}}
