import {compareCodePoints} from '@society0/host/order';
import {BoundActions} from './selection.js';
import type {SelectionLease} from '@society0/host/slots';
import type {Invocation,Lease,Json} from '../contracts/common.js';
import type {ActionDefinition,ActionExecutionContext,ActionReceipt,ActionScope,ActionSelection} from '../contracts/domain.js';
export { ActionExecutionFailure, validateInput } from './execution.js';
import { ActionCall, validateInput, type ActionAuthority, type ActionRegistration, type OfferState } from './execution.js';
import { computeOffers, type OfferFilter } from './offers.js';
import { offerKey, schemaCard, schemaLine, targetText, DEFAULT_OFFER_LABELS, type ActionRef, type ActionSchemaCard, type OfferLimits, type OfferProvider, type OfferSet, type TargetRef } from '../contracts/affordance.js';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({
  maxDiscovered: Schema.natural().min(1).default(100),
  defaultTimeoutMs: Schema.natural().min(1).default(30000),
  maxOffers: Schema.natural().min(1).default(64),
  maxOfferBytes: Schema.natural().min(1).default(3072),
  maxSchemaBytes: Schema.natural().min(1).default(4096),
  maxAlternatives: Schema.natural().min(1).default(5),
  labels: Schema.object({
    full: Schema.string().default(DEFAULT_OFFER_LABELS.full),
    change: Schema.string().default(DEFAULT_OFFER_LABELS.change),
    prefill: Schema.string().default(DEFAULT_OFFER_LABELS.prefill),
    locked: Schema.string().default(DEFAULT_OFFER_LABELS.locked),
    needs: Schema.string().default(DEFAULT_OFFER_LABELS.needs),
    omitted: Schema.string().default(DEFAULT_OFFER_LABELS.omitted),
    listPath: Schema.string().default(DEFAULT_OFFER_LABELS.listPath),
  }),
});
export const metadata: PluginMetadata = { "title": "actions", "summary": "动态发现、执行与可供性；执行上下文的transaction在最终写事务检查主体/动作资格，跨插件共享原生tx。", "configVersion": 2, "configPolicy": [
  { "path": "/maxDiscovered", "mode": "boundary", "affectsResults": true, "secret": false },
  { "path": "/defaultTimeoutMs", "mode": "boundary", "affectsResults": true, "secret": false },
  { "path": "/maxOffers", "mode": "boundary", "affectsResults": true, "secret": false },
  { "path": "/maxOfferBytes", "mode": "boundary", "affectsResults": true, "secret": false },
  { "path": "/maxSchemaBytes", "mode": "boundary", "affectsResults": true, "secret": false },
  { "path": "/maxAlternatives", "mode": "boundary", "affectsResults": true, "secret": false },
  { "path": "/labels", "mode": "recreate", "affectsResults": true, "secret": false }
], "scope": "generation", "allowedScopes": ["generation"], "hostAbi": "society0/host/1" };
export const api: ApiEntry = { "exportPath": "./actions", "provides": [{ "key": "actions", "protocol": "actions", "major": 1, "capabilities": [] }], "requires": [{ "service": { "key": "artifacts", "protocol": "artifacts", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }, { "service": { "key": "slots", "protocol": "slots", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }, { "service": { "key": "database", "protocol": "database", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }, { "service": { "key": "attempts", "protocol": "attempts", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }, { "service": { "key": "actorEligibility", "protocol": "actorEligibility", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }] };
import { Context, Service } from '@society0/cordis';
import { randomUUID } from 'node:crypto';
import { Ajv, type ValidateFunction } from 'ajv';
import { slotKey } from '@society0/host/slots';
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
const offerKeySlot = slotKey<OfferProvider>('society/action-offers');
export function actionsMigration(source: Readonly<Record<string, unknown>>): SqliteMigration { return { owner: 'actions', from: 0, to: 2, after: [], source, statements: [{ sql: 'CREATE TABLE action_receipts(operation_id TEXT PRIMARY KEY NOT NULL,invocation_id TEXT NOT NULL,actor_id TEXT NOT NULL,moment_id TEXT NOT NULL,action TEXT NOT NULL,registration_id TEXT NOT NULL,input TEXT NOT NULL,value TEXT NOT NULL,target TEXT NULL,offer_key TEXT NULL,provider TEXT NULL); CREATE INDEX action_receipts_actor ON action_receipts(actor_id,operation_id); CREATE INDEX action_receipts_invocation ON action_receipts(invocation_id); CREATE INDEX action_receipts_target ON action_receipts(actor_id,action,target)' }] }; }
export function actionsReceiptsMigration(source: Readonly<Record<string, unknown>>): SqliteMigration { return { owner: 'actions', from: 1, to: 2, after: [], source, statements: [{ sql: 'ALTER TABLE action_receipts ADD COLUMN target TEXT NULL; ALTER TABLE action_receipts ADD COLUMN offer_key TEXT NULL; ALTER TABLE action_receipts ADD COLUMN provider TEXT NULL; CREATE INDEX action_receipts_target ON action_receipts(actor_id,action,target)' }] }; }
declare module '@society0/cordis' {
    interface Context {
        actions: Actions;
    }
}
type Reader = SqliteReadView | SqliteTransaction;
export class Actions extends Service {
    static Config = Config;
    static inject = ['slots', 'database', 'attempts', 'actorEligibility', 'artifacts'];
    private validator = new Ajv({ strict: false, allErrors: true });
    private registryClock = new RegistryClock();
    readonly metrics = { eligibilityScans: 0, keysetCandidates: 0, offerProviderCalls: 0 };
    constructor(ctx: Context, private options: {
        maxDiscovered: number;
        defaultTimeoutMs: number;
        maxOffers: number;
        maxOfferBytes: number;
        maxSchemaBytes: number;
        maxAlternatives: number;
        labels: typeof DEFAULT_OFFER_LABELS;
    }) {
        super(ctx, 'actions');
        ctx.slots.declare({ key, strategy: 'keyed', required: false, validate: (v: any) => {
                if (typeof v?.definition?.execute !== 'function' || typeof v?.definition?.available !== 'function')
                    return fail('ACTION_INVALID');
                return v;
            } });
        ctx.slots.declare({ key: offerKeySlot, strategy: 'keyed', required: false, validate: (v: any) => {
                if (typeof v?.id !== 'string' || !Array.isArray(v.actions) || typeof v.offers !== 'function' || typeof v.versions !== 'function')
                    return fail('OFFER_PROVIDER_INVALID');
                return v;
            } });
    }
    register(definition: ActionDefinition) { const card = schemaCard(definition.name, { description: definition.description, input: definition.inputSchema, targets: definition.targets }); if (Buffer.byteLength(schemaLine(card)) > this.options.maxSchemaBytes) return fail('ACTION_SCHEMA_BYTES'); const input = this.validator.compile(definition.inputSchema as object), output = this.validator.compile(definition.outputSchema as object); return this.ctx.slots.contribute(key, { id: definition.name, value: { definition, registrationId: randomUUID(), input, output } }); }
    provide(provider: OfferProvider) { return this.ctx.slots.contribute(offerKeySlot, { id: provider.id, value: provider }); }
    private async decide(action: string, input: Json | null, scope: ActionScope, reader: Reader, logicalTime: Json, discovery: boolean, target?: TargetRef) { this.metrics.eligibilityScans++; const eligibility = this.ctx.get('actorEligibility') as ActorEligibility; return eligibility.evaluate({ actorId: scope.actorId, momentId: scope.momentId, logicalTime, ...(discovery ? { kind: 'action-discovery' as const, action, ...(target ? { target } : {}) } : { kind: 'action' as const, action, input: input ?? null, ...(target ? { target } : {}) }) }, reader); }
    private async allowed(action: string, input: Json | null, scope: ActionScope, reader: Reader, logicalTime: Json, discovery = false, target?: TargetRef) { return (await this.decide(action, input, scope, reader, logicalTime, discovery, target)).allowed; }
    version() {
        return this.registryClock.capture(this.ctx.slots.acquire(key, { generation: 'read' }));
    }
    private limits(maxOffers = this.options.maxOffers): OfferLimits { return { maxOffers, maxOfferBytes: this.options.maxOfferBytes, maxSchemaBytes: this.options.maxSchemaBytes }; }
    private async compute(selection: SelectionLease<ActionRegistration>, providers: readonly OfferProvider[], scope: ActionScope, view: Reader, limits: OfferLimits, filter?: OfferFilter): Promise<OfferSet> {
        const actions = selection.items.map((item) => item.value.definition);
        const query = { actorId: scope.actorId, momentId: scope.momentId, ...(scope.logicalTime !== undefined ? { logicalTime: scope.logicalTime } : {}), maxItems: limits.maxOffers };
        const versions = [{ owner: 'actions', version: (await this.version()).version }, { owner: 'view', version: 'revision' in view ? view.revision : view.baseRevision }];
        for (const provider of providers)
            versions.push(...await provider.versions(query, view));
        const targetAware = (this.ctx.get('actorEligibility') as ActorEligibility & { targetAware?: boolean }).targetAware === true;
        this.metrics.offerProviderCalls += providers.length;
        return computeOffers({ scope, actions, providers, targetAware, versions, limits, ...(filter ? { filter } : {}), gate: (action, target) => this.allowed(action, null, scope, view, scope.logicalTime ?? null, true, target) }, view);
    }
    private async discoverSelected(selection:SelectionLease<ActionRegistration>,scope:DiscoveryScope,view:SqliteReadView,request:PageRequest,registry:unknown){bounds(request);request={...request,maxItems:Math.min(request.maxItems,this.options.maxDiscovered)};const id=identity(scope,view,registry),last=after(request.cursor,id);let total=0;const items=[];for(const entry of [...selection.items].sort((a,b)=>compareCodePoints(a.id,b.id))){if((entry.value.definition.exposure??'offer')!=='offer')continue;if(!await this.allowed(entry.id,null,scope,view,scope.logicalTime??null,true)||!await entry.value.definition.available(scope,view))continue;total++;if(last!==undefined&&compareCodePoints(entry.id,String(last))<=0)continue;this.metrics.keysetCandidates++;if(items.length<=request.maxItems){const {name,inputSchema,outputSchema,effect}=entry.value.definition;items.push({name,inputSchema,outputSchema,effect})}}return page(items,String(total),view.revision,request,id,item=>item.name)}
    async discover(scope:DiscoveryScope,view:SqliteReadView,request:PageRequest){const selection=await this.ctx.slots.acquire(key,{generation:'read'});try{return await this.discoverSelected(selection,scope,view,request,this.registryClock.observe(selection.items.map(item=>item.value.registrationId)))}finally{await selection.release()}}
    async offers(scope: DiscoveryScope, view: SqliteReadView, limits: OfferLimits): Promise<OfferSet> {
        const selection = await this.ctx.slots.acquire(key, { generation: 'read' });
        const providerLease = await this.ctx.slots.acquire(offerKeySlot, { generation: 'read' });
        try {
            return await this.compute(selection, providerLease.items.map((item) => item.value), scope, view, limits);
        }
        finally {
            await providerLease.release();
            await selection.release();
        }
    }
    async offersFor(scope: DiscoveryScope, view: SqliteReadView, filter: { readonly targets: readonly TargetRef[]; readonly actions?: readonly string[] }): Promise<readonly ActionRef[]> {
        const selection = await this.ctx.slots.acquire(key, { generation: 'read' });
        const providerLease = await this.ctx.slots.acquire(offerKeySlot, { generation: 'read' });
        try {
            const set = await this.compute(selection, providerLease.items.map((item) => item.value), scope, view, this.limits(), { targets: filter.targets, ...(filter.actions ? { actions: filter.actions } : {}) });
            return set.offers.map((offer) => ({ act: offer.action, ...(offer.target ? { target: targetText(offer.target) } : {}), ...(offer.prefill !== undefined ? { prefill: offer.prefill } : {}), ...(offer.needs ? { needs: offer.needs } : {}) }));
        }
        finally {
            await providerLease.release();
            await selection.release();
        }
    }
    async describe(name: string): Promise<ActionSchemaCard | undefined> {
        const selection = await this.ctx.slots.acquire(key, { generation: 'read' });
        try {
            const definition = selection.items.find((item) => item.id === name)?.value.definition;
            return definition ? schemaCard(definition.name, { description: definition.description, input: definition.inputSchema, targets: definition.targets }) : undefined;
        }
        finally {
            await selection.release();
        }
    }
    private targetIssue(definition: ActionDefinition, target?: TargetRef): string | undefined {
        if (!definition.targets)
            return target ? 'ACTION_TARGET_INVALID' : undefined;
        if (!target)
            return definition.targets.required ? 'ACTION_TARGET_INVALID' : undefined;
        return definition.targets.kinds.includes(target.kind) ? undefined : 'ACTION_TARGET_INVALID';
    }
    private rejection(context: ActivationContext, code: string, action: string, target?: TargetRef): ActionReceipt {
        return { operationId: context.operationId, status: 'rejected', value: { code, action, ...(target ? { target: targetText(target) } : {}) }, original: null, artifacts: [], requiredOperations: [] };
    }
    /** 执行期复查：先按 action 请求判门槛与 available，再看当前 offer 是否存在。 */
    private async currentOffer(selection: SelectionLease<ActionRegistration>, providers: readonly OfferProvider[], entry: ActionRegistration, scope: ActionScope, view: Reader, name: string, input: Json, target?: TargetRef): Promise<OfferState> {
        if (!await this.allowed(name, input, scope, view, scope.logicalTime ?? null, false, target))
            return 'ineligible';
        if (!await entry.definition.available(scope, view))
            return 'ineligible';
        // exposure:'tool' 是固定工具的执行后端，不参与 offer 目录；门槛和 available 已在上方判定。
        if ((entry.definition.exposure ?? 'offer') !== 'offer')
            return 'valid';
        const set = await this.compute(selection, providers, scope, view, this.limits(1), { action: name, ...(target ? { targets: [target] } : {}) });
        return set.offers.some((offer) => offerKey(offer) === offerKey({ action: name, target })) ? 'valid' : 'absent';
    }
    private async executeSelected(selection: SelectionLease<ActionRegistration>, name: string, input: Json, context: ActivationContext, target?: TargetRef): Promise<ActionReceipt> {
        context = { ...context, signal: AbortSignal.any([context.signal, AbortSignal.timeout(this.options.defaultTimeoutMs)]) };
        context.signal.throwIfAborted();
        const entry = selection.items.find((item) => item.id === name)?.value;
        if (!entry)
            return fail('ACTION_UNAVAILABLE');
        const issue = this.targetIssue(entry.definition, target);
        if (issue)
            return this.rejection(context, issue, name, target);
        const scope: ActionScope = { actorId: context.actorId, momentId: context.momentId, logicalTime: context.logicalTime, query: null, ...(target ? { target } : {}) };
        const providerLease = await this.ctx.slots.acquire(offerKeySlot, { generation: 'read' });
        const view = await context.database.snapshot({ maxAgeMs: 30000 });
        try {
            const providers = providerLease.items.map((item) => item.value);
            const offerSet = await this.compute(selection, providers, scope, view, this.limits(1), { action: name, ...(target ? { targets: [target] } : {}) });
            const offer = offerSet.offers.find((item) => offerKey(item) === offerKey({ action: name, target }));
            const prefill = offer?.prefill;
            const locked = offer?.locked ?? [];
            if (locked.length && input !== null && typeof input === 'object' && !Array.isArray(input)) {
                for (const field of locked) {
                    if (Object.prototype.hasOwnProperty.call(input, field) && JSON.stringify((input as Readonly<Record<string, Json>>)[field]) !== JSON.stringify(prefill !== null && typeof prefill === 'object' && !Array.isArray(prefill) ? (prefill as Readonly<Record<string, Json>>)[field] : undefined))
                        return this.rejection(context, 'ACTION_INPUT_LOCKED', name, target);
                }
            }
            const effective: Json = prefill !== null && typeof prefill === 'object' && !Array.isArray(prefill) && input !== null && typeof input === 'object' && !Array.isArray(input) ? { ...prefill, ...input } : input;
            const invalid = validateInput(entry, effective, context.operationId);
            if (invalid)
                return invalid;
            const authority: ActionAuthority = {
                key: offerKey({ action: name, target }),
                ...(target ? { target } : {}),
                ...(offer ? { offer: { key: offerKey({ action: name, target }), provider: offer.provider } } : {}),
                delivered: context.deliveredOffers,
                state: (reader) => this.currentOffer(selection, providers, entry, scope, reader, name, effective, target),
                findings: async (reader) => {
                    const decision = await this.decide(name, effective, scope, reader, context.logicalTime, true, target);
                    return decision.findings.filter((finding) => !finding.finding.allowed).map((finding) => ({ ruleId: finding.ruleId, reason: finding.finding.reason }));
                },
                alternatives: async (reader) => {
                    const alternatives = await this.compute(selection, providers, scope, reader, this.limits(this.options.maxAlternatives + 1), { action: name });
                    return alternatives.offers.filter((item) => offerKey(item) !== offerKey({ action: name, target })).slice(0, this.options.maxAlternatives).map((item) => ({ act: item.action, ...(item.target ? { target: targetText(item.target) } : {}), ...(item.prefill !== undefined ? { prefill: item.prefill } : {}), ...(item.needs ? { needs: item.needs } : {}) }));
                },
            };
            return new ActionCall(this.ctx, entry, effective, context, authority).run();
        }
        finally {
            await view.close();
            await providerLease.release();
        }
    }
    async execute(name:string,input:Json,context:ActivationContext,target?:TargetRef):Promise<ActionReceipt>{const selected=await this.acquire(context);try{return await selected.value.execute(name,input,context,target)}finally{await selected.release()}}
    async acquire(context:Invocation):Promise<Lease<ActionSelection>>{const selection=await this.ctx.slots.acquire(key,context),registry=this.registryClock.observe(selection.items.map(item=>item.value.registrationId));const bound=new BoundActions((scope,view,request)=>this.discoverSelected(selection,scope,view,request,registry),(name,input,activation,target)=>this.executeSelected(selection,name,input,activation,target),()=>selection.release());return {value:bound,release:()=>bound.release()}}
}
export default Actions;
