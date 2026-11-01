import {compareCodePoints} from '@society0/host/order';
import {BoundActions} from './selection.js';
import type {SelectionLease} from '@society0/host/slots';
import type {Invocation,Lease,Json} from '../contracts/common.js';
import type {ActionSelection} from '../contracts/domain.js';
export { ActionExecutionFailure } from './execution.js';
import { ActionCall, type ActionRegistration } from './execution.js';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({ maxDiscovered: Schema.natural().min(1).default(100), defaultTimeoutMs: Schema.natural().min(1).default(30000) });
export const metadata: PluginMetadata = { "title": "actions", "summary": "动态发现与执行行动；执行上下文的transaction在最终写事务检查主体/动作资格，跨插件共享原生tx。", "configVersion": 1, "configPolicy": [{ "path": "/maxDiscovered", "mode": "boundary", "affectsResults": true, "secret": false }, { "path": "/defaultTimeoutMs", "mode": "boundary", "affectsResults": true, "secret": false }], "scope": "generation", "allowedScopes": ["generation"], "hostAbi": "society0/host/1" };
export const api: ApiEntry = { "exportPath": "./actions", "provides": [{ "key": "actions", "protocol": "actions", "major": 1, "capabilities": [] }], "requires": [{ "service": { "key": "artifacts", "protocol": "artifacts", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }, { "service": { "key": "slots", "protocol": "slots", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }, { "service": { "key": "database", "protocol": "database", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }, { "service": { "key": "attempts", "protocol": "attempts", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }, { "service": { "key": "actorEligibility", "protocol": "actorEligibility", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }] };
import { Context, Service } from '@society0/cordis';
import { randomUUID } from 'node:crypto';
import { Ajv, type ValidateFunction } from 'ajv';
import { slotKey } from '@society0/host/slots';
import type { ActionDefinition, ActionExecutionContext, ActionReceipt } from '../contracts/domain.js';
import type { ActivationContext } from '../contracts/runtime.js';
import type { DiscoveryScope, PageRequest } from '../contracts/data.js';
import type { SqliteReadView, SqliteTransaction, SqliteMigration, TransactionRequest } from '../sqlite/contracts.js';
import type { ActorEligibility } from '../contracts/eligibility.js';
import { after, identity, page, fail, bounds, bytes, RegistryClock } from '../information/page.js';
import '../sqlite/index.js';
import '../attempts/index.js';
import '../artifacts-local/index.js';
import { prepareValue, attachValue } from '../results/payload.js';
const key = slotKey<ActionRegistration>('society/action');
export function actionsMigration(source: Readonly<Record<string, unknown>>): SqliteMigration { return { owner: 'actions', from: 0, to: 1, after: [], source, statements: [{ sql: 'CREATE TABLE action_receipts(operation_id TEXT PRIMARY KEY NOT NULL,invocation_id TEXT NOT NULL,actor_id TEXT NOT NULL,moment_id TEXT NOT NULL,action TEXT NOT NULL,registration_id TEXT NOT NULL,input TEXT NOT NULL,value TEXT NOT NULL); CREATE INDEX action_receipts_actor ON action_receipts(actor_id,operation_id); CREATE INDEX action_receipts_invocation ON action_receipts(invocation_id)' }] }; }
declare module '@society0/cordis' {
    interface Context {
        actions: Actions;
    }
}
export class Actions extends Service {
    static Config = Config;
    static inject = ['slots', 'database', 'attempts', 'actorEligibility', 'artifacts'];
    private validator = new Ajv({ strict: false, allErrors: true });
    private registryClock = new RegistryClock();
    readonly metrics = { eligibilityScans: 0, keysetCandidates: 0 };
    constructor(ctx: Context, private options: {
        maxDiscovered: number;
        defaultTimeoutMs: number;
    }) {
        super(ctx, 'actions');
        ctx.slots.declare({ key, strategy: 'keyed', required: false, validate: (v: any) => {
                if (typeof v?.definition?.execute !== 'function' || typeof v?.definition?.available !== 'function')
                    return fail('ACTION_INVALID');
                return v;
            } });
    }
    register(definition: ActionDefinition) { const input = this.validator.compile(definition.inputSchema as object), output = this.validator.compile(definition.outputSchema as object); return this.ctx.slots.contribute(key, { id: definition.name, value: { definition, registrationId: randomUUID(), input, output } }); }
    private async allowed(action: string, input: any, scope: DiscoveryScope, reader: SqliteReadView | SqliteTransaction, logicalTime: any, discovery = false) { const eligibility = this.ctx.get('actorEligibility') as ActorEligibility; return (await eligibility.evaluate({ actorId: scope.actorId, momentId: scope.momentId, logicalTime, ...(discovery ? { kind: 'action-discovery' as const, action } : { kind: 'action' as const, action, input }) }, reader)).allowed; }
    version() {
        return this.registryClock.capture(this.ctx.slots.acquire(key, { generation: 'read' }));
    }
    private async discoverSelected(selection:SelectionLease<ActionRegistration>,scope:DiscoveryScope,view:SqliteReadView,request:PageRequest,registry:unknown){bounds(request);request={...request,maxItems:Math.min(request.maxItems,this.options.maxDiscovered)};const id=identity(scope,view,registry),last=after(request.cursor,id);let total=0;const items=[];for(const entry of [...selection.items].sort((a,b)=>compareCodePoints(a.id,b.id))){this.metrics.eligibilityScans++;if(!await this.allowed(entry.id,null,scope,view,scope.logicalTime??null,true)||!await entry.value.definition.available(scope,view))continue;total++;if(last!==undefined&&compareCodePoints(entry.id,String(last))<=0)continue;this.metrics.keysetCandidates++;if(items.length<=request.maxItems){const {name,inputSchema,outputSchema,effect}=entry.value.definition;items.push({name,inputSchema,outputSchema,effect})}}return page(items,String(total),view.revision,request,id,item=>item.name)}
    async discover(scope:DiscoveryScope,view:SqliteReadView,request:PageRequest){const selection=await this.ctx.slots.acquire(key,{generation:'read'});try{return await this.discoverSelected(selection,scope,view,request,this.registryClock.observe(selection.items.map(item=>item.value.registrationId)))}finally{await selection.release()}}
    private async executeSelected(selection:SelectionLease<ActionRegistration>,name:string,input:Json,context:ActivationContext):Promise<ActionReceipt>{context={...context,signal:AbortSignal.any([context.signal,AbortSignal.timeout(this.options.defaultTimeoutMs)])};context.signal.throwIfAborted();const entry=selection.items.find(item=>item.id===name)?.value;if(!entry)return fail('ACTION_UNAVAILABLE');const scope={actorId:context.actorId,momentId:context.momentId,logicalTime:context.logicalTime,query:null};return new ActionCall(this.ctx,entry,input,context,reader=>this.allowed(name,input,scope,reader,context.logicalTime)).run()}
    async execute(name:string,input:Json,context:ActivationContext):Promise<ActionReceipt>{const selected=await this.acquire(context);try{return await selected.value.execute(name,input,context)}finally{await selected.release()}}
    async acquire(context:Invocation):Promise<Lease<ActionSelection>>{const selection=await this.ctx.slots.acquire(key,context),registry=this.registryClock.observe(selection.items.map(item=>item.value.registrationId));const bound=new BoundActions((scope,view,request)=>this.discoverSelected(selection,scope,view,request,registry),(name,input,activation)=>this.executeSelected(selection,name,input,activation),()=>selection.release());return {value:bound,release:()=>bound.release()}}
}
export default Actions;
