import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({ bodyInlineBytes: Schema.natural().min(1).default(16384), maxMessageBytes: Schema.natural().min(1).default(16777216) });
export const metadata: PluginMetadata = { "title": "thread", "summary": "完整保留消息链、工具调用和结果、interactionId、原始provider字段与perception cursor；大正文使用对象引用。", "configVersion": 1, "configPolicy": [{ "path": "/bodyInlineBytes", "mode": "new-run", "affectsResults": true, "secret": false }, { "path": "/maxMessageBytes", "mode": "new-run", "affectsResults": true, "secret": false }], "scope": "run", "allowedScopes": ["run"], "hostAbi": "society0/host/1" };
export const api: ApiEntry = { "exportPath": "./thread", "provides": [{ "key": "threads", "protocol": "threads", "major": 1, "capabilities": [] }], "requires": [{ "service": { "key": "database", "protocol": "database", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }, { "service": { "key": "artifacts", "protocol": "artifacts", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }] };
import { Context, Service } from '@society0/cordis';
import type { Message, ConsumerPosition, PreparedMessage } from '../contracts/domain.js';
import type { Json } from '../contracts/common.js';
import type { StoredValue, PayloadRef, PayloadRange, DiscoveryScope, PageRequest } from '../contracts/data.js';
import type { SqliteReadView, SqliteTransaction } from '../sqlite/contracts.js';
import { attachValue, readValue, prepareBoundedJson, jsonByteBound } from '../results/payload.js';
import { assertWellFormed } from '../contracts/text.js';
import { after, identity, page, fail, equal, bounds, sqlPageBudget, assertSqlPagePrefix } from '../information/page.js';
import '../artifacts-local/index.js';
export * from './schema.js';
export type { PreparedMessage } from '../contracts/domain.js';
const queryBounds = { maxRows: 1, maxBytes: 65536 };
declare module '@society0/cordis' {
    interface Context {
        threads: Threads;
    }
}
export class Threads extends Service {
    static Config = Config;
    static inject = ['database', 'artifacts'];
    constructor(ctx: Context, private options: {
        bodyInlineBytes: number;
        maxMessageBytes: number;
    }) { super(ctx, 'threads'); }
    async open(actor: string, moment: string, view: SqliteReadView) { assertWellFormed(actor,'actorId','THREAD_TEXT_NOT_WELL_FORMED');assertWellFormed(moment,'momentId','THREAD_TEXT_NOT_WELL_FORMED'); const [row] = await view.query({ sql: 'SELECT id,head,interaction_id FROM threads WHERE actor_id=? AND moment_id=?', parameters: [actor, moment] }, queryBounds); const id = row ? String(row.id) : JSON.stringify([view.runId, actor, moment]); return { id, actorId: actor, momentId: moment, interactionId: row ? String(row.interaction_id) : id, headSeq: String(row?.head ?? 0), messageCount: String(row?.head ?? 0) }; }
    async prepare(message: Message, signal?: AbortSignal): Promise<PreparedMessage> {
        assertWellFormed(message.id,'message.id','THREAD_TEXT_NOT_WELL_FORMED');
        let payload: StoredValue;
        try {
            payload = await prepareBoundedJson(message as unknown as Json, { inline: this.options.bodyInlineBytes, maxBytes: this.options.maxMessageBytes }, this.ctx.artifacts, signal);
        }
        catch (error) {
            const code = (error as { code?: string }).code;
            if (code === 'PAYLOAD_JSON_BYTES')
                return fail('THREAD_MESSAGE_BYTES');
            if (code === 'PAYLOAD_JSON_VALUE')
                return fail('THREAD_MESSAGE_INVALID');
            throw error;
        }
        return { id: message.id, interactionId: message.interactionId, role: message.role, payload, references: this.references(message) };
    }
    private requireInline(payload: StoredValue) {
        if (payload.kind !== 'inline')
            return;
        let size: number;
        try {
            size = jsonByteBound(payload.value, this.options.bodyInlineBytes);
        }
        catch (error) {
            if ((error as { code?: string }).code === 'PAYLOAD_JSON_VALUE')
                return fail('THREAD_MESSAGE_INVALID');
            throw error;
        }
        if (size > this.options.bodyInlineBytes)
            return fail('THREAD_PREPARE_REQUIRED');
    }
    private references(message: Message) { return [message.content, message.originalProviderFields].filter((value): value is import('../artifacts-local/store.js').ArtifactRef => !!value && typeof value === 'object' && !Array.isArray(value) && 'id' in value && 'bytes' in value && 'mediaType' in value); }
    private async address(thread: string, reader: SqliteReadView | SqliteTransaction) { const [row] = await reader.query({ sql: 'SELECT actor_id,moment_id,interaction_id FROM threads WHERE id=?', parameters: [thread] }, queryBounds); if (row)
        return { actor: String(row.actor_id), moment: String(row.moment_id), interaction: String(row.interaction_id) }; const value = JSON.parse(thread); if (!Array.isArray(value) || value.length !== 3 || value.some(x => typeof x !== 'string'))
        return fail('THREAD_ID'); return { actor: value[1] as string, moment: value[2] as string, interaction: thread }; }
    async owner(thread: string, reader: SqliteReadView | SqliteTransaction) { return (await this.address(thread, reader)).actor; }
    async append(tx: SqliteTransaction, thread: string, message: Message | PreparedMessage) {
        const { actor, moment, interaction } = await this.address(thread, tx);
        if (message.interactionId !== interaction)
            return fail('THREAD_INTERACTION');
        const payload: StoredValue = 'payload' in message ? message.payload : { kind: 'inline', value: message as unknown as Json };
        this.requireInline(payload);
        await attachValue(tx, payload, this.ctx.artifacts);
        for (const ref of ('payload' in message ? message.references ?? [] : this.references(message)))
            await this.ctx.artifacts.reference(tx, ref);
        await tx.execute({ sql: 'INSERT INTO threads VALUES(?,?,?,?,0) ON CONFLICT(id) DO NOTHING', parameters: [thread, actor, moment, message.interactionId] });
        await tx.execute({ sql: 'INSERT INTO thread_messages SELECT id,head+1,?,?,? FROM threads WHERE id=?', parameters: [message.id, message.role, JSON.stringify(payload), thread] });
        await tx.execute({ sql: 'UPDATE threads SET head=head+1 WHERE id=?', parameters: [thread] });
    }
    async consumer(thread: string, consumerId: string, reader: SqliteReadView | SqliteTransaction): Promise<ConsumerPosition | undefined> { const [row] = await reader.query({ sql: 'SELECT cursor FROM thread_consumers WHERE thread_id=? AND consumer_id=?', parameters: [thread, consumerId] }, queryBounds); return row ? { consumerId, cursor: JSON.parse(String(row.cursor)) } : undefined; }
    async appendInput(tx: SqliteTransaction, request: {
        threadId: string;
        expectedConsumer: ConsumerPosition | null;
        nextConsumer: ConsumerPosition;
        message: Message | PreparedMessage;
        contexts: readonly StoredValue[];
    }) {
        const current = await this.consumer(request.threadId, request.nextConsumer.consumerId, tx);
        if (!equal(current ?? null, request.expectedConsumer))
            return fail('THREAD_CONSUMER_STALE');
        await this.append(tx, request.threadId, request.message);
        for (const [ordinal, value] of request.contexts.entries()) {
            await attachValue(tx, value, this.ctx.artifacts);
            await tx.execute({ sql: 'INSERT INTO thread_contexts VALUES(?,?,?,?)', parameters: [request.threadId, request.message.id, ordinal, JSON.stringify(value)] });
        }
        await tx.execute({ sql: 'INSERT INTO thread_consumers VALUES(?,?,?) ON CONFLICT(thread_id,consumer_id) DO UPDATE SET cursor=excluded.cursor', parameters: [request.threadId, request.nextConsumer.consumerId, JSON.stringify(request.nextConsumer.cursor)] });
    }
    /** SQL先选编码字节有界的连续前缀；首行超预算保留空哨兵，交由领域层明确拒绝。 */
    private async pageRows(thread:string,last:string,view:SqliteReadView,request:PageRequest,descending:boolean){
        const direction=descending?'DESC':'ASC',comparison=descending?'<':'>';
        const {limit,budget,rowReserve,nativeBounds}=sqlPageBudget(request);
        const rows=await view.query({sql:`WITH candidates AS (
SELECT seq,length(CAST(json_object('seq',seq,'message_id',message_id,'role',role,'payload',payload) AS BLOB))+${rowReserve} AS row_bytes
FROM thread_messages WHERE thread_id=? AND seq ${comparison} ? ORDER BY seq ${direction} LIMIT ?
), prefix AS (
SELECT seq,sum(row_bytes) OVER (ORDER BY seq ${direction} ROWS UNBOUNDED PRECEDING) AS used,row_number() OVER (ORDER BY seq ${direction}) AS ordinal FROM candidates
)
SELECT p.seq,CASE WHEN p.used<=? THEN m.message_id END AS message_id,CASE WHEN p.used<=? THEN m.role END AS role,CASE WHEN p.used<=? THEN m.payload END AS payload
FROM prefix p JOIN thread_messages m ON m.thread_id=? AND m.seq=p.seq WHERE p.used<=? OR p.ordinal=1 ORDER BY p.seq ${direction}`,
parameters:[thread,BigInt(last),limit,budget,budget,budget,thread,budget]}, nativeBounds);
        assertSqlPagePrefix(rows);
        return rows;
    }
    async page(thread: string, scope: DiscoveryScope, view: SqliteReadView, request: PageRequest, descending = false) {
        bounds(request);
        const [stored] = await view.query({ sql: 'SELECT actor_id,moment_id FROM threads WHERE id=? AND actor_id=?', parameters: [thread, scope.actorId] }, queryBounds);
        const opened = await this.open(scope.actorId, stored ? String(stored.moment_id) : scope.momentId, view);
        if (opened.id !== thread)
            return fail('THREAD_NOT_VISIBLE');
        const id = identity(scope, view, { thread, descending, head: opened.headSeq });
        const last = after(request.cursor, id) ?? (descending ? String(BigInt(opened.headSeq) + 1n) : '0');
        const rows=await this.pageRows(thread,last,view,request,descending),tail=rows.at(-1);
        const more=tail!==undefined&&(descending?BigInt(String(tail.seq))>1n:BigInt(String(tail.seq))<BigInt(opened.headSeq));
        return page(rows.map(r=>({seq:String(r.seq),id:String(r.message_id),role:String(r.role),payload:JSON.parse(String(r.payload)) as StoredValue})),opened.messageCount,view.revision,request,id,x=>x.seq,more);
    }
    tail(thread: string, scope: DiscoveryScope, view: SqliteReadView, request: PageRequest) { return this.page(thread, scope, view, request, true); }
    readPayload(ref: PayloadRef, range: PayloadRange, signal?: AbortSignal) { return readValue(ref, range, this.ctx.artifacts, signal); }
}
export default Threads;
