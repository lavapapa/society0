import type {ActionSelection,ActionReceipt} from '../contracts/domain.js';import type {ActivationContext} from '../contracts/runtime.js';
import type {DiscoveryScope,PageRequest,Page} from '../contracts/data.js';import type {SqliteReadView} from '../sqlite/contracts.js';import type {Json} from '../contracts/common.js';
import {fail} from '../information/page.js';
type Discover=ActionSelection['discover'];type Execute=ActionSelection['execute'];
/** schema与调用共享同一原生selection，撤销等待已接纳调用及证据保存。 */
export class BoundActions implements ActionSelection {
 private closing=false;private closed?:Promise<void>;private active=new Set<Promise<unknown>>();
 constructor(private discoverSelected:Discover,private executeSelected:Execute,private releaseSelected:()=>Promise<void>){}
 private async run<T>(body:()=>Promise<T>):Promise<T>{if(this.closing)return fail('ACTION_SELECTION_RELEASED');const work=body();this.active.add(work);try{return await work}finally{this.active.delete(work)}}
 discover(scope:DiscoveryScope,view:SqliteReadView,request:PageRequest){return this.run(()=>this.discoverSelected(scope,view,request))}
 execute(name:string,input:Json,context:ActivationContext):Promise<ActionReceipt>{return this.run(()=>this.executeSelected(name,input,context))}
 release(){return this.closed??=(async()=>{this.closing=true;await Promise.allSettled([...this.active]);await this.releaseSelected()})()}
}
