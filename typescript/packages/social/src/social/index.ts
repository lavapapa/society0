import { Context, Service } from '@society0/host';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({
    propagation: Schema.string().default('neighbor-broadcast/v1'), mediaEnabled: Schema.boolean().default(true), contentLengthLimit: Schema.union([Schema.const(-1), Schema.natural()]).default(250),
    pairingGroups: Schema.array(Schema.array(Schema.string())).default([]), pairingSessionMinutes: Schema.natural().min(1).max(120).default(10),
    offerPosts: Schema.natural().min(1).default(16), wakeDriver: Schema.string().default('llm/v1')
});
export const metadata: PluginMetadata = {
    "title": "social", "summary": "social", "scope": "generation", "hostAbi": "society0/host/1", "configVersion": 1, "configPolicy": [{
            "path": "/propagation", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/mediaEnabled", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/contentLengthLimit", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/pairingGroups", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/pairingSessionMinutes", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/offerPosts", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/wakeDriver", "mode": "new-run", "affectsResults": true
        }]
};
export const api: ApiEntry = {
    "exportPath": "./social", "provides": [{
            "key": "social", "protocol": "social", "major": 1, "capabilities": []
        }, {
            "key": "socialPresentation", "protocol": "socialPresentation", "major": 1, "capabilities": []
        }], "requires": [{
            "service": {
                "key": "actions", "protocol": "actions", "major": 1, "capabilities": []
            }, "optional": false, "phase": "module"
        }, {
            "service": {
                "key": "information", "protocol": "information", "major": 1, "capabilities": []
            }, "optional": false, "phase": "module"
        }, {
            "service": {
                "key": "actors", "protocol": "actors", "major": 1, "capabilities": []
            }, "optional": false, "phase": "module"
        }, {
            "service": {
                "key": "socialRepository", "protocol": "socialRepository", "major": 1, "capabilities": []
            }, "optional": false, "phase": "module"
        }, {
            "service": {
                "key": "slots", "protocol": "slots", "major": 1, "capabilities": []
            }, "optional": false, "phase": "module"
        }]
};
import { slotKey } from '@society0/host/slots';
import { randomUUID } from 'node:crypto';
import '@society0/core/actions';
import '@society0/core/information';
import '../social-repository/index.js';
import type { ActionDefinition, ActionExecutionContext } from '@society0/core/contracts/domain';
import type { Random } from '@society0/core/contracts/random';
import type { StoredValue } from '@society0/core/contracts/data';
import type { PublicWriter } from '@society0/core/contracts/public';
import type { SelfProvider } from '@society0/core/contracts/public-self';
import { canonicalLine } from '@society0/core/public-self';
import type { PropagationStrategy, Json, PresentationRequest, SqliteTransaction, SqliteReadView, DiscoveryScope, PageRequest, Page, NewPost } from '../contracts.js';
import { transactionRequest, page, sized, after } from '../paging.js';
import { socialTime } from '../clock.js';
import { SocialOffers } from '../social-offers/index.js';
import { postsDecl, trendingDecl, notificationDecl, edgeDecl, postRow, trendingRow, publicProfile, type SocialPublicPost, type SocialProfile } from './public.js';
function socialTextFault(field: string) { return Object.assign(new Error(`SOCIAL_TEXT_NOT_WELL_FORMED: ${field} contains a lone surrogate`), { code: 'SOCIAL_TEXT_NOT_WELL_FORMED', field }); }
export const propagationSlot = slotKey<PropagationStrategy>('society/social-propagation');
declare module '@society0/host' {
    interface Context {
        social: Social;
        socialPresentation: Pick<Social, 'present'>;
    }
}
export interface InterventionRequest {
    time: Json;
    targetHashtag: string;
    interventionRate: number;
    tagToApply: string;
}
export interface InterventionReport {
    ruleType: 'intervention';
    targetHashtag: string;
    interventionRate: number;
    tagApplied: string;
    totalPosts: string;
    postsFlagged: number;
    flaggedPostIds: readonly string[];
    timestamp: string;
}
export interface PairingView {
    actorId: string;
    group: number;
    currentPartner: string | null;
    currentRound: number;
    totalRounds: number;
    canConverse: boolean;
    partnerHistory: readonly string[];
    sessionDurationMinutes: number;
}
export interface PairingRoundResult {
    round: number;
    pairs: readonly (readonly [string, string])[];
    successfulPairs: number;
}
export interface PairingMessage {
    id: string;
    ordinal: string;
    sender: string;
    receiver: string;
    round: number;
    createdTime: string;
    content: string;
}
export class Social extends Service {
    static Config = Config;
    static inject = ['actions', 'information', 'actors', 'socialRepository', 'slots'];
    private config: ReturnType<typeof Config>;
    constructor(ctx: Context, input = {}) {
        super(ctx, 'social');
        this.config = Config(input);
        this.assertPairingConfig();
        ctx.provide('socialPresentation', {
            present: (tx: SqliteTransaction, request: PresentationRequest) => this.present(tx, request)
        });
        ctx.slots.declare({
            key: propagationSlot, strategy: 'keyed', required: true, validate: (v: unknown) => {
                if (typeof (v as PropagationStrategy)?.recipients !== 'function')
                    throw Error('SOCIAL_PROPAGATION');
                return v as PropagationStrategy;
            }
        });
        this.installActions();
        this.installPairingActions();
        this.installInformation();
        this.installOffers();
    }
    async [Service.init]() {
        this.installPublicTables();
        this.installSelfProviders();
    }
    private publicService() {
        return this.ctx.get('public', false) as { registry: { table(decl: typeof postsDecl): () => void }, writer(tx: SqliteTransaction): PublicWriter } | undefined;
    }
    private installPublicTables() {
        this.ctx.inject(['public'], scoped => {
            const service = scoped.get('public') as { registry: { table(decl: typeof postsDecl): () => void } };
            const disposers = [service.registry.table(postsDecl), service.registry.table(trendingDecl)];
            scoped.effect(() => () => {
                for (const dispose of disposers)
                    dispose();
            });
        });
    }
    private async projectPost(tx: SqliteTransaction, post: SocialPublicPost, parent?: SocialPublicPost): Promise<void> {
        const service = this.publicService();
        if (!service)
            return;
        const writer = service.writer(tx);
        await writer.upsert(postsDecl.path, [postRow(post)]);
        const rows = [trendingRow(post)];
        if (parent)
            rows.push(trendingRow(parent));
        await writer.upsert(trendingDecl.path, rows);
    }
    private async writePublicProfile(tx: SqliteTransaction, actorId: string): Promise<void> {
        const service = this.publicService();
        if (!service)
            return;
        const profile = await this.ctx.socialRepository.profile(actorId, tx) as SocialProfile;
        const body = Buffer.from(JSON.stringify(publicProfile(profile)) + '\n');
        await service.writer(tx).publish('/public/social/profiles/' + encodeURIComponent(actorId) + '.json', body, {
            media: 'application/json', meta: { title: '公开资料 ' + actorId }
        });
    }
    private installSelfProviders() {
        this.ctx.inject(['publicSelf'], scoped => {
            const service = scoped.get('publicSelf') as { provide(provider: SelfProvider): () => void };
            const disposers = [
                service.provide(this.notificationsProvider()),
                service.provide(this.edgesProvider('/self/social/following.jsonl', 'following')),
                service.provide(this.edgesProvider('/self/social/followers.jsonl', 'followers')),
                service.provide(this.profileProvider())
            ];
            scoped.effect(() => () => {
                for (const dispose of disposers)
                    dispose();
            });
        });
    }
    private selfVisible(actorId: string, view: SqliteReadView) {
        return this.ctx.socialRepository.member(view, actorId).then(Boolean);
    }
    private notificationsProvider(): SelfProvider {
        const repository = this.ctx.socialRepository;
        return {
            path: '/self/social/notifications.jsonl', media: 'application/jsonl',
            visible: (actorId, view) => this.selfVisible(actorId, view),
            versions: (actorId, view) => repository.versions(view, ['social/notifications']),
            render: async function* (actorId, view, signal) {
                const rows = await view.query({
                    sql: 'SELECT id,kind,created_time,consumed,data FROM social_notifications WHERE actor=? ORDER BY ordinal',
                    parameters: [actorId]
                }, { maxRows: 100000, maxBytes: 16 * 1024 * 1024 });
                for (const row of rows) {
                    signal.throwIfAborted();
                    yield Buffer.from(canonicalLine(notificationDecl, {
                        id: String(row.id), actorId, kind: String(row.kind), createdTime: String(row.created_time),
                        consumed: !!row.consumed, data: JSON.parse(String(row.data)) as Json
                    }) + '\n');
                }
            }
        };
    }
    private edgesProvider(path: string, direction: 'following' | 'followers'): SelfProvider {
        const column = direction === 'following' ? 'follower' : 'followee';
        const repository = this.ctx.socialRepository;
        return {
            path, media: 'application/jsonl',
            visible: (actorId, view) => this.selfVisible(actorId, view),
            versions: (actorId, view) => repository.versions(view, ['social/relations']),
            render: async function* (actorId, view, signal) {
                const rows = await view.query({
                    sql: `SELECT ordinal,follower,followee,created_time FROM social_edges WHERE ${column}=? ORDER BY ordinal`,
                    parameters: [actorId]
                }, { maxRows: 100000, maxBytes: 16 * 1024 * 1024 });
                for (const row of rows) {
                    signal.throwIfAborted();
                    yield Buffer.from(canonicalLine(edgeDecl, {
                        id: String(row.ordinal), from: String(row.follower), to: String(row.followee), createdTime: String(row.created_time)
                    }) + '\n');
                }
            }
        };
    }
    private profileProvider(): SelfProvider {
        const repository = this.ctx.socialRepository;
        return {
            path: '/self/profile.json', media: 'application/json',
            visible: (actorId, view) => this.selfVisible(actorId, view),
            versions: (actorId, view) => repository.versions(view, ['social/members', 'social/content', 'social/relations', 'social/notifications', 'social/preferences']),
            render: async function* (actorId, view, signal) {
                const profile = await repository.profile(actorId, view);
                if (!profile)
                    return;
                signal.throwIfAborted();
                yield Buffer.from(JSON.stringify(profile) + '\n');
            }
        };
    }
    private installOffers() {
        const actions = ['publish', 'reply', 'repost', 'react', 'follow', 'unfollow', 'preferences', 'consumeNotifications', 'present']
            .filter(name => this.config.mediaEnabled || !['publish', 'reply', 'repost', 'react'].includes(name))
            .map(name => 'social.' + name);
        const provider = new SocialOffers({ repository: this.ctx.socialRepository, posts: this.config.offerPosts, actions });
        this.ctx.effect(() => this.ctx.actions.provide(provider));
    }
    present(tx: SqliteTransaction, request: PresentationRequest) {
        return this.ctx.socialRepository.present(tx, request);
    }
    /** tick 边界批处理：等待本代已登记的批处理完成。 */
    afterTick() {
        return this.ctx.socialRepository.afterTick();
    }
    /**
     * 规则干预：对正文包含目标话题标签的帖子按研究随机流抽样施加标记标签。
     * 事务由调用者提供，抽样地址固定为 (run, actor, operation, stream, post ordinal)，
     * 因此重放、分叉和重复调用得到同一结果。
     */
    async intervene(tx: SqliteTransaction, request: InterventionRequest): Promise<InterventionReport> {
        const time = socialTime(request.time);
        if (typeof request.targetHashtag !== 'string' || !request.targetHashtag)
            throw Error('SOCIAL_INTERVENTION_HASHTAG');
        if (typeof request.tagToApply !== 'string' || !request.tagToApply)
            throw Error('SOCIAL_INTERVENTION_TAG');
        if (typeof request.interventionRate !== 'number' || !Number.isFinite(request.interventionRate) || request.interventionRate < 0 || request.interventionRate > 1)
            throw Error('SOCIAL_INTERVENTION_RATE');
        const random = this.ctx.get('random') as Random | undefined;
        if (!random)
            throw Error('SOCIAL_INTERVENTION_RANDOM_REQUIRED');
        const database = this.ctx.get('database') as { runId?: string } | undefined;
        const [all] = await tx.query({
            sql: 'SELECT count(*) AS n FROM social_posts'
        }, { maxRows: 1, maxBytes: 1024 });
        const [count] = await tx.query({
            sql: 'SELECT count(*) AS n FROM social_posts WHERE instr(content,?)>0', parameters: [request.targetHashtag]
        }, { maxRows: 1, maxBytes: 1024 });
        const matched = Number(count!.n), total = Number(all!.n), rows = await tx.query({
            sql: 'SELECT ordinal,id FROM social_posts WHERE instr(content,?)>0 ORDER BY ordinal', parameters: [request.targetHashtag]
        }, { maxRows: Math.max(1,matched), maxBytes: matched * 128 + 4096 });
        const flagged: string[] = [];
        for (const row of rows) {
            const [tagged] = await tx.query({
                sql: 'SELECT 1 AS present FROM social_tags WHERE post=? AND tag=?', parameters: [String(row.id), request.tagToApply]
            }, { maxRows: 1, maxBytes: 1024 });
            if (tagged) continue;
            const draw = random.at({
                run: database?.runId ?? '', actor: 'social/intervention', operation: 'intervention', stream: request.targetHashtag, index: String(row.ordinal)
            }).uniform;
            if (draw >= request.interventionRate) continue;
            await tx.execute({ sql: 'INSERT INTO social_tags VALUES(?,?)', parameters: [String(row.id), request.tagToApply] });
            flagged.push(String(row.id));
        }
        if (flagged.length) await this.ctx.socialRepository.bump(tx, 'social/content');
        return {
            ruleType: 'intervention', targetHashtag: request.targetHashtag, interventionRate: request.interventionRate,
            tagApplied: request.tagToApply, totalPosts: String(total), postsFlagged: flagged.length,
            flaggedPostIds: flagged, timestamp: time
        };
    }
    private assertPairingConfig() {
        const groups = this.config.pairingGroups as string[][];
        if (!groups.length)
            return;
        const size = groups[0]!.length, seen = new Set<string>();
        if (size < 2 || size > 20 || size % 2)
            throw Error('SOCIAL_PAIRING_CONFIG');
        for (const group of groups) {
            if (group.length !== size)
                throw Error('SOCIAL_PAIRING_CONFIG');
            for (const actor of group) {
                if (!actor || seen.has(actor))
                    throw Error('SOCIAL_PAIRING_CONFIG');
                seen.add(actor);
            }
        }
    }
    private async ensurePairing(tx: SqliteTransaction) {
        const [head] = await tx.query({
            sql: 'SELECT current_round,total_rounds FROM social_pair_head WHERE id=1'
        }, { maxRows: 1, maxBytes: 1024 });
        if (head)
            return;
        const groups = this.config.pairingGroups as string[][];
        if (!groups.length)
            throw Error('SOCIAL_PAIRING_CONFIG');
        const size = groups[0]!.length, total = size - 1;
        await tx.execute({
            sql: 'INSERT INTO social_pair_head VALUES(1,?,?,?,0)', parameters: [total ? 1 : 0, total, this.config.pairingSessionMinutes]
        });
        let ordinal = 0;
        for (const [groupNo, group] of groups.entries()) {
            for (const [index, actor] of group.entries())
                await tx.execute({
                    sql: 'INSERT INTO social_pair_members VALUES(?,?,?,?,NULL,0)', parameters: [actor, groupNo, index, total ? 1 : 0]
                });
            for (const [round, pairs] of schedule(group).entries())
                for (const [first, second] of pairs)
                    await tx.execute({
                        sql: 'INSERT INTO social_pair_schedule VALUES(?,?,?,?,?)', parameters: [ordinal++, groupNo, round + 1, first, second]
                    });
        }
    }
    /** 开始一轮配对；同一轮重入保留既有配对，拒绝回退。 */
    async startRound(tx: SqliteTransaction, round: number): Promise<PairingRoundResult> {
        await this.ensurePairing(tx);
        const [head] = await tx.query({
            sql: 'SELECT current_round,total_rounds FROM social_pair_head WHERE id=1'
        }, { maxRows: 1, maxBytes: 1024 });
        const current = Number(head!.current_round), total = Number(head!.total_rounds);
        if (!Number.isSafeInteger(round) || round < 1 || round > total || round < current)
            throw Error('SOCIAL_PAIRING_ROUND');
        if (round === current) {
            const [active] = await tx.query({
                sql: 'SELECT 1 AS present FROM social_pair_members WHERE active=1 LIMIT 1'
            }, { maxRows: 1, maxBytes: 1024 });
            if (active)
                return { round, pairs: [], successfulPairs: 0 };
        }
        await tx.execute({ sql: 'UPDATE social_pair_head SET current_round=? WHERE id=1', parameters: [round] });
        await tx.execute({ sql: 'UPDATE social_pair_members SET partner=NULL,round_no=?,active=0', parameters: [round] });
        const scheduleRows = await tx.query({
            sql: 'SELECT first,second FROM social_pair_schedule WHERE round_no=? ORDER BY ordinal', parameters: [round]
        }, { maxRows: total * 10 + 1, maxBytes: 1048576 });
        const pairs: [string, string][] = [];
        for (const row of scheduleRows) {
            const first = String(row.first), second = String(row.second);
            const [a, b] = first < second ? [first, second] : [second, first];
            const [started] = await tx.query({
                sql: 'SELECT 1 AS present FROM social_pair_started WHERE first=? AND second=?', parameters: [a, b]
            }, { maxRows: 1, maxBytes: 1024 });
            if (started)
                continue;
            await tx.execute({ sql: 'INSERT INTO social_pair_started VALUES(?,?,?)', parameters: [a, b, round] });
            for (const [actor, partner] of [[first, second], [second, first]])
                await tx.execute({
                    sql: 'UPDATE social_pair_members SET partner=?,active=1 WHERE actor=?', parameters: [partner, actor]
                });
            const [history] = await tx.query({
                sql: 'SELECT coalesce(max(ordinal),0)+1 AS n FROM social_pair_history'
            }, { maxRows: 1, maxBytes: 1024 });
            await tx.execute({
                sql: 'INSERT INTO social_pair_history VALUES(?,?,?,?)', parameters: [Number(history!.n), first, second, round]
            });
            const [next] = await tx.query({
                sql: 'SELECT coalesce(max(ordinal),0)+1 AS n FROM social_pair_history'
            }, { maxRows: 1, maxBytes: 1024 });
            await tx.execute({
                sql: 'INSERT INTO social_pair_history VALUES(?,?,?,?)', parameters: [Number(next!.n), second, first, round]
            });
            pairs.push([first, second]);
        }
        return { round, pairs, successfulPairs: pairs.length };
    }
    /** 推进到下一轮；本轮尚未开始的配对由后续 startRound 决定。 */
    async advanceRound(tx: SqliteTransaction) {
        await this.ensurePairing(tx);
        const [head] = await tx.query({
            sql: 'SELECT current_round,total_rounds FROM social_pair_head WHERE id=1'
        }, { maxRows: 1, maxBytes: 1024 });
        const current = Number(head!.current_round), total = Number(head!.total_rounds);
        if (current >= total)
            return { status: 'completed' as const, newRound: current };
        const next = current + 1;
        await tx.execute({ sql: 'UPDATE social_pair_head SET current_round=? WHERE id=1', parameters: [next] });
        await tx.execute({ sql: 'UPDATE social_pair_members SET partner=NULL,round_no=?,active=0', parameters: [next] });
        const rows = await tx.query({
            sql: 'SELECT first,second FROM social_pair_schedule WHERE round_no=? ORDER BY ordinal', parameters: [next]
        }, { maxRows: total * 10 + 1, maxBytes: 1048576 });
        return {
            status: 'advanced' as const, newRound: next,
            pairingsAvailable: rows.map(row => [String(row.first), String(row.second)] as [string, string])
        };
    }
    /** 重置当前轮收件箱可见起点；历史消息仍保留。 */
    async resetRoundMessages(tx: SqliteTransaction, round: number) {
        await this.ensurePairing(tx);
        const [head] = await tx.query({
            sql: 'SELECT current_round FROM social_pair_head WHERE id=1'
        }, { maxRows: 1, maxBytes: 1024 });
        if (!Number.isSafeInteger(round) || round !== Number(head!.current_round))
            throw Error('SOCIAL_PAIRING_ROUND');
        const [tail] = await tx.query({
            sql: 'SELECT coalesce(max(ordinal),0) AS n FROM social_messages'
        }, { maxRows: 1, maxBytes: 1024 });
        await tx.execute({ sql: 'UPDATE social_pair_head SET inbox_after=? WHERE id=1', parameters: [Number(tail!.n)] });
        return { status: 'initialized' as const, round };
    }
    async pairing(reader: SqliteReadView | SqliteTransaction, actorId: string): Promise<PairingView | undefined> {
        const [head] = await reader.query({
            sql: 'SELECT current_round,total_rounds,session_minutes FROM social_pair_head WHERE id=1'
        }, { maxRows: 1, maxBytes: 1024 });
        if (!head)
            return undefined;
        const [member] = await reader.query({
            sql: 'SELECT group_no,partner,round_no,active FROM social_pair_members WHERE actor=?', parameters: [actorId]
        }, { maxRows: 1, maxBytes: 1024 });
        if (!member)
            return undefined;
        const [historyCount] = await reader.query({
            sql: 'SELECT count(*) AS n FROM social_pair_history WHERE actor=?', parameters: [actorId]
        }, { maxRows: 1, maxBytes: 1024 });
        const history = await reader.query({
            sql: 'SELECT partner FROM social_pair_history WHERE actor=? ORDER BY ordinal', parameters: [actorId]
        }, { maxRows: Math.max(1, Number(historyCount!.n)), maxBytes: Number(historyCount!.n) * 128 + 4096 });
        return {
            actorId, group: Number(member.group_no), currentPartner: member.partner === null ? null : String(member.partner),
            currentRound: Number(member.round_no), totalRounds: Number(head.total_rounds), canConverse: !!member.active,
            partnerHistory: history.map(row => String(row.partner)), sessionDurationMinutes: Number(head.session_minutes)
        };
    }
    async groupView(reader: SqliteReadView | SqliteTransaction, actorId: string) {
        const view = await this.pairing(reader, actorId);
        if (!view)
            return undefined;
        const members = await reader.query({
            sql: 'SELECT actor FROM social_pair_members WHERE group_no=? ORDER BY ordinal', parameters: [view.group]
        }, { maxRows: 1000, maxBytes: 65536 });
        const [planCount] = await reader.query({
            sql: 'SELECT count(*) AS n FROM social_pair_schedule WHERE group_no=? AND round_no>=? AND (first=? OR second=?)',
            parameters: [view.group, view.currentRound, actorId, actorId]
        }, { maxRows: 1, maxBytes: 1024 });
        const plan = await reader.query({
            sql: 'SELECT round_no,first,second FROM social_pair_schedule WHERE group_no=? AND round_no>=? AND (first=? OR second=?) ORDER BY round_no,ordinal',
            parameters: [view.group, view.currentRound, actorId, actorId]
        }, { maxRows: Math.max(1, Number(planCount!.n)), maxBytes: Number(planCount!.n) * 128 + 4096 });
        return {
            members: members.map(row => String(row.actor)),
            upcoming: plan.map(row => ({
                round: Number(row.round_no), partner: String(row.first) === actorId ? String(row.second) : String(row.first)
            })),
            currentRound: view.currentRound
        };
    }
    async conversation(reader: SqliteReadView | SqliteTransaction, actorId: string, request: PageRequest, history = false) {
        const [head] = await reader.query({
            sql: 'SELECT current_round,inbox_after FROM social_pair_head WHERE id=1'
        }, { maxRows: 1, maxBytes: 1024 });
        const identity = { kind: history ? 'conversation-history' : 'conversation', runId: (reader as SqliteReadView).runId, actorId, round: head ? Number(head.current_round) : 0 };
        const last = after(request.cursor, identity) || '0';
        if (!head) {
            return page([], '0', (reader as SqliteReadView).revision ?? '0', request, identity, (item: PairingMessage) => item.ordinal);
        }
        const current = Number(head.current_round), inbox = Number(head.inbox_after);
        const where = history ? 'receiver=?' : 'receiver=? AND round_no=? AND ordinal>?';
        const parameters: (string | number | bigint)[] = history ? [actorId] : [actorId, current, inbox];
        const [count] = await reader.query({
            sql: `SELECT count(*) AS n FROM social_messages WHERE ${where}`, parameters
        }, { maxRows: 1, maxBytes: 1024 });
        const rows = await reader.query({
            sql: `SELECT * FROM social_messages WHERE ${where} AND ordinal>? ORDER BY ordinal LIMIT ?`,
            parameters: [...parameters, BigInt(last), request.maxItems + 1]
        }, { maxRows: request.maxItems + 1, maxBytes: Math.max(65536, request.maxResponseBytes * 2) });
        const items: PairingMessage[] = rows.map(row => ({
            id: String(row.id), ordinal: String(row.ordinal), sender: String(row.sender), receiver: String(row.receiver),
            round: Number(row.round_no), createdTime: String(row.created_time), content: String(row.content)
        }));
        return page(items, String(count!.n), (reader as SqliteReadView).revision ?? '0', request, identity, item => item.ordinal);
    }
    private async message(tx: SqliteTransaction, context: ActionExecutionContext, input: any, broadcast: boolean) {
        if (typeof input.content !== 'string' || !input.content.trim())
            throw Error('SOCIAL_MESSAGE_EMPTY');
        if (Buffer.byteLength(input.content) > this.ctx.socialRepository.maxContentBytes)
            throw Error('SOCIAL_MESSAGE_BUDGET');
        const [member] = await tx.query({
            sql: 'SELECT group_no,partner,round_no,active FROM social_pair_members WHERE actor=?', parameters: [context.actorId]
        }, { maxRows: 1, maxBytes: 1024 });
        if (!member || !member.active)
            throw Error('SOCIAL_PAIRING_INACTIVE');
        const round = Number(member.round_no), receivers = broadcast ? (await tx.query({
            sql: 'SELECT actor FROM social_pair_members WHERE group_no=? AND actor<>? ORDER BY ordinal', parameters: [Number(member.group_no), context.actorId]
        }, { maxRows: 1000, maxBytes: 65536 })).map(row => String(row.actor)) : [String(member.partner)];
        const delivered: { receiver: string; messageId: string }[] = [];
        for (const receiver of receivers) {
            const id = context.operationId + '/' + receiver;
            await tx.execute({
                sql: 'INSERT INTO social_messages(id,sender,receiver,round_no,created_time,content) VALUES(?,?,?,?,?,?)',
                parameters: [id, context.actorId, receiver, round, socialTime(context.logicalTime), input.content]
            });
            delivered.push({ receiver, messageId: id });
        }
        return broadcast ? { round, delivered } : { round, sentTo: String(member.partner), messageId: delivered[0]!.messageId, delivered };
    }
    private async pairingActive(reader: SqliteReadView | SqliteTransaction, actorId: string) {
        return !!(await this.pairing(reader, actorId))?.canConverse;
    }
    private async emitWake(delivered: readonly { receiver: string; messageId: string }[], context: ActionExecutionContext, kind: 'dm' | 'broadcast') {
        const activation = this.ctx.get('activation') as { wake?: (signals: readonly unknown[], context: unknown) => Promise<unknown> } | undefined;
        if (!activation?.wake || !delivered.length)
            return;
        const signals = delivered.map(item => ({
            kind,
            actor: item.receiver,
            moment: context.momentId,
            source: 'social/' + kind,
            id: context.operationId + '/wake/' + item.receiver,
            from: context.actorId,
            driver: this.config.wakeDriver,
            requiresResponse: kind === 'dm',
            input: { messageId: item.messageId, sender: context.actorId },
        }));
        await activation.wake(signals, { ...context, lease: context.writeLease, selections: [] });
    }
    private installPairingActions() {
        if (!this.config.pairingGroups.length)
            return;
        const properties = { content: { type: 'string' } } as Record<string, Json>;
        for (const [name, broadcast] of [['sendPartnerMessage', false], ['broadcastToGroup', true]] as const) {
            this.ctx.actions.register({
                name: 'social.' + name,
                inputSchema: { type: 'object', properties, required: ['content'], additionalProperties: false },
                outputSchema: { type: 'object' },
                effect: 'world',
                available: async (scope, reader) => this.pairingActive(reader, scope.actorId),
                execute: async (input, context) => {
                    this.assertText(properties, input);
                    const operationId = context.operationId + '/social/' + name;
                    const result = await context.transaction(transactionRequest(operationId), tx => this.message(tx, context, input, broadcast));
                    await this.emitWake((result.value as { delivered: { receiver: string; messageId: string }[] }).delivered, context, broadcast ? 'broadcast' : 'dm');
                    return { operationId, status: 'completed', value: result.value as Json, original: input, artifacts: [] };
                }
            });
        }
    }
    private async pairingInformation(input: Json, scope: DiscoveryScope, view: SqliteReadView, request: PageRequest) {
        const value = await this.pairing(view, scope.actorId);
        return page(value ? [value as unknown as Json] : [], value ? '1' : '0', view.revision ?? '0', request, { kind: 'pairing', actorId: scope.actorId }, () => '1');
    }
    private async pairingPlanInformation(input: Json, scope: DiscoveryScope, view: SqliteReadView, request: PageRequest) {
        const value = await this.groupView(view, scope.actorId);
        return page(value ? [value as unknown as Json] : [], value ? '1' : '0', view.revision ?? '0', request, { kind: 'pairing-plan', actorId: scope.actorId }, () => '1');
    }
    private assertText(properties: Record<string, any>, input: any) {
        for (const [field, property] of Object.entries(properties)) {
            const value = input[field];
            if (value === undefined)
                continue;
            if (property.type === 'string' && !value.isWellFormed())
                throw socialTextFault(field);
            if (property.type === 'array' && property.items?.type === 'string')
                value.forEach((item: string, index: number) => {
                    if (!item.isWellFormed())
                        throw socialTextFault(`${field}[${index}]`);
                });
        }
    }
    private installActions() {
        const repository = this.ctx.socialRepository;
        const available = async (scope: {
            actorId: string;
        }, reader: any) => !!await repository.member(reader, scope.actorId);
        const outputSchema = {
            type: 'object'
        } as const;
        const definitions: Record<string, {
            properties: Record<string, Json>;
            required: string[];
            targets?: { kinds: string[]; required: boolean };
            exposure?: 'offer' | 'tool' | 'internal';
            execute: (input: any, context: ActionExecutionContext, tx: SqliteTransaction) => Promise<unknown>;
        }> = {
            publish: {
                properties: {
                    content: {
                        type: 'string'
                    }, tags: {
                        type: 'array', items: {
                            type: 'string'
                        }
                    }, id: {
                        type: 'string'
                    }, metadata: {}
                }, required: ['content'], execute: async (input, context, tx) => this.publish(input, context, tx, 'post')
            },
            reply: {
                properties: {
                    postId: {
                        type: 'string'
                    }, content: {
                        type: 'string'
                    }, id: {
                        type: 'string'
                    }
                }, required: ['postId', 'content'], targets: {
                    kinds: ['social/post'], required: false
                }, execute: async (input, context, tx) => this.publish(input, context, tx, 'reply')
            },
            repost: {
                properties: {
                    postId: {
                        type: 'string'
                    }, content: {
                        type: 'string'
                    }, id: {
                        type: 'string'
                    }
                }, required: ['postId'], targets: {
                    kinds: ['social/post'], required: false
                }, execute: async (input, context, tx) => this.publish(input, context, tx, 'repost')
            },
            react: {
                properties: {
                    postId: {
                        type: 'string'
                    }, kind: {
                        type: 'string'
                    }, remove: {
                        type: 'boolean'
                    }
                }, required: ['postId', 'kind'], targets: {
                    kinds: ['social/post'], required: false
                }, execute: (input, context, tx) => this.reactPost(input, context, tx)
            },
            follow: {
                properties: {
                    actorId: {
                        type: 'string'
                    }
                }, required: ['actorId'], targets: {
                    kinds: ['social/actor'], required: false
                }, execute: (input, context, tx) => this.followActor(input, context, tx, true)
            },
            unfollow: {
                properties: {
                    actorId: {
                        type: 'string'
                    }
                }, required: ['actorId'], targets: {
                    kinds: ['social/actor'], required: false
                }, execute: (input, context, tx) => this.followActor(input, context, tx, false)
            },
            preferences: {
                properties: {
                    value: {}
                }, required: ['value'], execute: async (input, context, tx) => {
                    await repository.setPreferences(tx, context.actorId, input.value);
                    await this.writePublicProfile(tx, context.actorId);
                    return {
                        updated: true
                    };
                }
            },
            consumeNotifications: {
                properties: {
                    ids: {
                        type: 'array', items: {
                            type: 'string'
                        }
                    }
                }, required: ['ids'], targets: {
                    kinds: ['social/notification'], required: false
                }, execute: async (input, context, tx) => {
                    await repository.consumeNotifications(tx, context.actorId, input.ids);
                    return {
                        consumed: input.ids
                    };
                }
            },
            present: {
                exposure: 'internal',
                properties: {
                    presentationId: {
                        type: 'string'
                    }, inputId: {
                        type: 'string'
                    }, versions: {
                        type: 'array', items: {
                            type: 'object'
                        }
                    }, itemIds: {
                        type: 'array', items: {
                            type: 'string'
                        }
                    }
                }, required: ['presentationId', 'inputId', 'versions', 'itemIds'], execute: async (input, context, tx) => {
                    await this.present(tx, {
                        ...input, actorId: context.actorId, momentId: context.momentId
                    });
                    return {
                        presented: input.itemIds
                    };
                }
            }
        };
        for (const [name, definition] of Object.entries(definitions)) {
            if (!this.config.mediaEnabled && ['publish', 'reply', 'repost', 'react'].includes(name))
                continue;
            const action: ActionDefinition = {
                name: 'social.' + name, inputSchema: {
                    type: 'object', properties: definition.properties, required: definition.required, additionalProperties: false
                }, outputSchema, effect: 'world', ...(definition.targets ? { targets: definition.targets } : {}), ...(definition.exposure ? { exposure: definition.exposure } : {}), available, execute: async (input, context) => {
                    this.assertText(definition.properties, input);
                    const operationId = context.operationId + '/social/' + name;
                    const result = await context.transaction(transactionRequest(operationId), tx => definition.execute(input, context, tx));
                    return {
                        operationId, status: 'completed', value: result.value as Json, original: input, artifacts: []
                    };
                }
            };
            this.ctx.actions.register(action);
        }
    }
    private installInformation() {
        const available = async (scope: {
            actorId: string;
        }, reader: SqliteReadView | SqliteTransaction) => !!await this.ctx.socialRepository.member(reader, scope.actorId);
        for (const id of ['feed', 'profile', 'network', 'notifications', 'posts', 'trending', ...(this.config.pairingGroups.length ? ['pairing', 'pairingPlan', 'conversation', 'conversationHistory', 'conversationMembers'] : [])])
            this.ctx.information.register({
                id: 'social/' + id, schema: id === 'posts' ? {
                    type: 'object', properties: { id: { type: 'string' }, authorId: { type: 'string' } }
                } : {
                    type: 'object'
                }, capabilities: ['page', 'original'], available, query: async (input, scope, view, request) => {
                    const result = await this.queryInformation(id, input, scope, view, request);
                    return sized({
                        ...result, items: result.items.map(value => ({
                            kind: 'inline', value: value as Json
                        } as StoredValue)), responseBytes: 0
                    }, request.maxResponseBytes);
                }
            });
    }
    private queryInformation(id: string, input: Json, scope: DiscoveryScope, view: SqliteReadView, request: PageRequest): Promise<Page<unknown>> {
        switch (id) {
            case 'feed': return this.ctx.socialRepository.feed(scope.actorId, view, request);
            case 'profile': return this.profileInformation(input, scope, view, request);
            case 'network': return this.networkInformation(input, scope, view, request);
            case 'notifications': return this.notificationInformation(input, scope, view, request);
            case 'posts': return this.postInformation(input, scope, view, request);
            case 'trending': return this.trendingInformation(input, view, request);
            case 'pairing': return this.pairingInformation(input, scope, view, request);
            case 'pairingPlan': return this.pairingPlanInformation(input, scope, view, request);
            case 'conversation': return this.conversation(view, scope.actorId, request);
            case 'conversationHistory': return this.conversation(view, scope.actorId, request, true);
            case 'conversationMembers': return this.pairingPlanInformation(input, scope, view, request);
            default: throw Error('SOCIAL_INFORMATION_SOURCE');
        }
    }
    private async profileInformation(input: Json, scope: DiscoveryScope, view: SqliteReadView, request: PageRequest) {
        const value = await this.ctx.socialRepository.profile((input as any)?.actorId ?? scope.actorId, view);
        return page([value], '1', view.revision, request, {
            actor: scope.actorId, revision: view.revision
        }, x => x.id);
    }
    private networkInformation(input: Json, scope: DiscoveryScope, view: SqliteReadView, request: PageRequest) {
        return this.ctx.socialRepository.neighbors(scope.actorId, view, request, (input as any)?.direction ?? 'following');
    }
    private notificationInformation(input: Json, scope: DiscoveryScope, view: SqliteReadView, request: PageRequest) {
        return this.ctx.socialRepository.notifications(scope.actorId, view, request, !!(input as any)?.unread);
    }
    private postInformation(input: Json, scope: DiscoveryScope, view: SqliteReadView, request: PageRequest) {
        const id = (input as any)?.id;
        return this.ctx.socialRepository.posts(view, request, id === undefined ? {
            authorId: (input as any)?.authorId ?? scope.actorId
        } : { id: String(id) });
    }
    private trendingInformation(input: Json, view: SqliteReadView, request: PageRequest) {
        return this.ctx.socialRepository.trending(view, request, {
            time: String((input as any)?.time ?? '0'), window: String((input as any)?.window ?? '100')
        });
    }
    private async publicationContent(input: any, tx: SqliteTransaction) {
        const parent = input.postId ? await this.ctx.socialRepository.get(input.postId, tx) : undefined;
        return input.content ?? parent?.content ?? '';
    }
    private newPost(input: any, context: ActionExecutionContext, kind: 'post' | 'reply' | 'repost', content: string): NewPost {
        return {
            id: input.id ?? randomUUID(), authorId: context.actorId, content, tags: input.tags ?? [], createdTime: socialTime(context.logicalTime), kind, parentId: input.postId ?? null, metadata: input.metadata ?? null
        };
    }
    private assertContentLength(content: string) {
        if (this.config.contentLengthLimit >= 0 && [...content].length > this.config.contentLengthLimit)
            throw Error('SOCIAL_CONTENT_LENGTH');
    }
    private async reactPost(input: any, context: ActionExecutionContext, tx: SqliteTransaction) {
        const result = await this.ctx.socialRepository.react(tx, {
            id: context.operationId, actorId: context.actorId, postId: input.postId, kind: input.kind, time: socialTime(context.logicalTime), remove: input.remove
        });
        if (result.changed) {
            const post = await this.ctx.socialRepository.get(input.postId, tx);
            if (post) {
                const service = this.publicService();
                if (service) {
                    const writer = service.writer(tx);
                    await writer.upsert(trendingDecl.path, [trendingRow(post)]);
                    await writer.upsert(postsDecl.path, [postRow(post)]);
                }
            }
        }
        return result;
    }
    private async followActor(input: any, context: ActionExecutionContext, tx: SqliteTransaction, enabled: boolean) {
        const result = await this.ctx.socialRepository.follow(tx, context.actorId, input.actorId, socialTime(context.logicalTime), context.operationId, enabled);
        if (result.changed) {
            await this.writePublicProfile(tx, context.actorId);
            await this.writePublicProfile(tx, input.actorId);
        }
        return result;
    }
    private async publish(input: any, context: ActionExecutionContext, tx: SqliteTransaction, kind: 'post' | 'reply' | 'repost') {
        const content = await this.publicationContent(input, tx);
        this.assertContentLength(content);
        const selection = await this.ctx.slots.acquire(propagationSlot, context);
        try {
            const strategy = selection.items.find(x => x.id === this.config.propagation)?.value;
            if (!strategy)
                throw Error('SOCIAL_PROPAGATION_MISSING');
            const recipients = await strategy.recipients({
                actorId: context.actorId, content: input
            }, this.ctx.socialRepository.readPort(tx));
            const post = await this.ctx.socialRepository.publish(tx, this.newPost(input, context, kind, content), recipients);
            const parent = post.parentId ? await this.ctx.socialRepository.get(post.parentId, tx) : undefined;
            await this.projectPost(tx, post, parent);
            await this.writePublicProfile(tx, post.authorId);
            if (parent && parent.authorId !== post.authorId)
                await this.writePublicProfile(tx, parent.authorId);
            return post;
        }
        finally {
            await selection.release();
        }
    }
}
/** 圆桌轮转：每轮每人恰好配对一次，n 人共 n-1 轮且任意两人恰好配对一次。 */
export function schedule(group: readonly string[]): [string, string][][] {
    const rotation = [...group], rounds: [string, string][][] = [];
    for (let round = 0; round < group.length - 1; round++) {
        const pairs: [string, string][] = [];
        for (let i = 0; i < group.length / 2; i++)
            pairs.push([rotation[i]!, rotation[group.length - 1 - i]!]);
        rounds.push(pairs);
        rotation.splice(0, rotation.length, rotation[0]!, rotation[rotation.length - 1]!, ...rotation.slice(1, -1));
    }
    return rounds;
}
export default Social;
