import {Context,Service} from '@society0/host';
import Schema from '@society0/schemastery';
import type {PluginMetadata,ApiEntry} from '@society0/host/module-resolver';
import {Type} from '@earendil-works/pi-ai';
import type {AgentTool} from '@earendil-works/pi-agent-core';
import type {ActivationContext} from '../contracts/runtime.js';
import type {ActionExecutionContext} from '../contracts/domain.js';
import type {ExplorationSession} from '../contracts/exploration.js';
import type {Json} from '../contracts/common.js';
import type {ActorToolBinding,ActorToolKind} from '../contracts/actor-tools.js';
import type {OfferFoldState,OfferLimits} from '../contracts/affordance.js';
import type {ReceiptLimits} from './receipts.js';
import {BoundToolExecution,boundSession,type BoundContext} from './binding.js';
export {actorToolsMigration} from './receipts.js';
import '../actions/index.js';
import '../exploration/index.js';
import '../exploration-shell/index.js';
import '../information/index.js';
import '../prompts/index.js';
import '../attempts/index.js';
import '../sqlite/index.js';
import '../artifacts-local/index.js';
export const Config=Schema.object({maxReadBytes:Schema.natural().min(1).default(65536),maxPageItems:Schema.natural().min(1).default(64),maxReceiptBytes:Schema.natural().min(1).default(1048576),maxToolCalls:Schema.natural().min(1).default(128),maxTransactionMs:Schema.natural().min(1).default(15000),legacyTools:Schema.array(Schema.union(['information','exploration','actions'])).default([])});
export const metadata:PluginMetadata={title:'actor-tools',summary:'实际Actions执行、冻结探索租约与独立持久回执',configVersion:1,configPolicy:['maxReadBytes','maxPageItems','maxReceiptBytes','maxToolCalls','maxTransactionMs','legacyTools'].map(key=>({path:'/'+key,mode:'recreate' as const,affectsResults:true,secret:false})),scope:'generation',hostAbi:'society0/host/1'};
export const api:ApiEntry={exportPath:'./actor-tools',provides:[{key:'actorTools',protocol:'actorTools',major:1,capabilities:[]}],requires:['actions','exploration','explorationShell','information','prompts','attempts','database','artifacts'].map(key=>({service:{key,protocol:key,major:1,capabilities:[]},optional:false,phase:'module' as const}))};
interface Options extends ReceiptLimits{maxReadBytes:number;maxPageItems:number;legacyTools:readonly ('information'|'exploration'|'actions')[]}
const baseSchemas={read:Type.Object({path:Type.String({description:'文件路径'}),offset:Type.Optional(Type.String({description:'起始字节偏移，十进制字符串；默认 0'})),length:Type.Optional(Type.Integer({minimum:1,description:'读取字节数；默认 maxReadBytes'}))}),find:Type.Object({root:Type.String({description:'起点路径'}),glob:Type.Optional(Type.String({description:'glob 模式；默认 *'})),cursor:Type.Optional(Type.String())}),grep:Type.Object({pattern:Type.String({description:'要搜索的原文'}),root:Type.Optional(Type.String({description:'搜索起点；默认 /'})),regex:Type.Optional(Type.Boolean({description:'按正则解释 pattern；默认 false'})),cursor:Type.Optional(Type.String())}),bash:Type.Object({command:Type.String({description:'命令；用于小文件和组合操作'})})};
const legacySchemas={information_discover:Type.Object({cursor:Type.Optional(Type.String())}),information_query:Type.Object({provider:Type.String(),query:Type.Any(),cursor:Type.Optional(Type.String())}),exploration_list:Type.Object({path:Type.String(),cursor:Type.Optional(Type.String())}),exploration_collect:Type.Object({path:Type.String(),query:Type.Any(),cursor:Type.Optional(Type.String())})};
const descriptions:Record<string,string>={read:'按字节范围读取路径内容，返回续读位置。',find:'列出或匹配路径；目录以 / 结尾。',grep:'在连续原文中做字面或正则搜索，输出 path:line:text。',bash:'执行小文件和组合命令；与 read/find/grep 同源。',information_discover:'发现信息提供者。',information_query:'查询信息提供者。',exploration_list:'列出路径。',exploration_collect:'收集结果。'};
declare module '@society0/host'{interface Context{actorTools:ActorTools}}
export class ActorTools extends Service {
 static Config=Config;static inject=['actions','exploration','explorationShell','information','prompts','attempts','database','artifacts'];constructor(ctx:Context,private config:Options){super(ctx,'actorTools')}
 private schemas(){const legacy=new Set(this.config.legacyTools);return {...baseSchemas,...(legacy.has('information')?{information_discover:legacySchemas.information_discover,information_query:legacySchemas.information_query}:{}),...(legacy.has('exploration')?{exploration_list:legacySchemas.exploration_list,exploration_collect:legacySchemas.exploration_collect}:{})}}
 [Service.init](){for(const [name,inputSchema]of Object.entries(this.schemas()))this.ctx.actions.register({name,inputSchema:inputSchema as unknown as Json,outputSchema:{},effect:name==='bash'?'workspace':'read',exposure:'tool',available:async()=>true,execute:async(input,context)=>{const value=await this.builtin(name,input,context as BoundContext);return {operationId:context.operationId,status:'completed',value:value as unknown as Json,original:null,artifacts:[]}}})}
 private scope(context:ActivationContext){return {actorId:context.actorId,momentId:context.momentId,logicalTime:context.logicalTime,query:null}}
 private page(input:any){return {cursor:input.cursor,maxItems:this.config.maxPageItems,maxResponseBytes:this.config.maxReadBytes}}
 private async builtin(name:string,input:any,context:BoundContext){if(name==='information_discover'||name==='information_query'){const view=await this.ctx.database.snapshot({maxAgeMs:30000});try{return name==='information_discover'?await this.ctx.information.discover(this.scope(context),view,this.page(input)):await this.ctx.information.query(input.provider,input.query,this.scope(context),view,this.page(input))}finally{await view.close()}}const selected=context[boundSession]?undefined:await this.ctx.exploration.acquire(this.scope(context),context);try{return await this.explore(name,input,context,context[boundSession]??selected!.value)}finally{await selected?.release()}}
 private async explore(name:string,input:any,context:ActionExecutionContext,session:ExplorationSession){return session.withView(view=>this.exploreInView(name,input,context,view))}
 private async exploreInView(name:string,input:any,context:ActionExecutionContext,session:ExplorationSession){
  if(name==='read')return this.readFile(session,input,context.signal);
  if(name==='find')return session.find({root:input.root,glob:input.glob??'*',page:this.page(input)});
  if(name==='grep')return session.grep({root:input.root??'/',pattern:input.pattern,mode:input.regex===true?'regex':'literal',page:this.page(input)});
  if(name==='bash')return this.ctx.explorationShell.execute(input.command,session,context);
  if(name==='exploration_list')return session.list(input.path,this.page(input));
  if(name==='exploration_collect')return session.collect(input.path,input.query,this.page(input));
  throw Error('TOOL_UNKNOWN');
 }
 private async readFile(session:ExplorationSession,input:any,signal:AbortSignal){const offset=String(input.offset??'0'),limit=Math.min(Number(input.length??this.config.maxReadBytes),this.config.maxReadBytes);if(!Number.isSafeInteger(limit)||limit<1)throw Error('TOOL_READ_BYTES');const node=await session.stat(input.path),size=node.rawBytes===undefined?undefined:Number(node.rawBytes),start=Number(offset);if(!Number.isSafeInteger(start)||start<0)throw Error('TOOL_READ_BYTES');if(size!==undefined&&start>=size)return {node,offset,text:''};const length=size===undefined?limit:Math.min(limit,size-start),held=await session.open(node);try{const chunks=[];for await(const chunk of held.value.read({offset,length,maxBytes:this.config.maxReadBytes},signal))chunks.push(chunk);return {node,offset,text:Buffer.concat(chunks).toString('utf8')}}finally{await held.release()}}
 private builtinTools(binding:BoundToolExecution){const schemas:Record<string,any>=this.schemas(),order=['bash','read','find','grep',...Object.keys(schemas).filter(name=>!['bash','read','find','grep'].includes(name))];const tools:Array<AgentTool & {kind:ActorToolKind}> = order.map(name=>{const parameters=schemas[name];const tool=binding.tool(name,name,parameters);return {...tool,description:descriptions[name]??tool.description,kind:'exploration' as const}});tools.push({name:'act',label:'act',kind:'action' as const,description:'执行一项当前可用的行动。可用行动及其参数见对话中的「可用行动」段；target 用其中给出的文本形式。',parameters:Type.Object({action:Type.String(),target:Type.Optional(Type.String({description:'形如 kind:id；无目标的行动省略'})),input:Type.Optional(Type.Object({},{additionalProperties:true,description:'行动参数；预填字段可省略'}))},{additionalProperties:false}),execute:(id,args,signal)=>binding.act(id,(args as {action:string}).action,((args as {input?:Json}).input??{}) as Json,(args as {target?:string}).target,signal)});return tools}
 async bind(context:ActivationContext,offers?:OfferFoldState,limits?:OfferLimits){const held:{release():Promise<void>}[]=[];try{const session=await this.ctx.exploration.acquire(this.scope(context),context);held.push(session);const actions=await this.ctx.actions.acquire(context);held.push(actions);const binding=new BoundToolExecution(this.ctx,context,this.config,session.value,actions.value,offers,limits),tools=this.builtinTools(binding);const value:ActorToolBinding={tools,actionReceipts:binding.receipts};return {value,release:async()=>{await Promise.all(held.map(item=>item.release()))}}}catch(error){await Promise.all(held.map(item=>item.release()));throw error}}
}
export default ActorTools;
