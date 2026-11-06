import { Context, Service } from '@society0/host';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({
    propagation: Schema.string().default('neighbor-broadcast/v1'), mediaEnabled: Schema.boolean().default(true), contentLengthLimit: Schema.union([Schema.const(-1), Schema.natural()]).default(250)
});
export const metadata: PluginMetadata = {
    "title": "social", "summary": "social", "scope": "generation", "hostAbi": "society0/host/1", "configVersion": 1, "configPolicy": [{
            "path": "/propagation", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/mediaEnabled", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/contentLengthLimit", "mode": "new-run", "affectsResults": true
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
import type { PropagationStrategy, Json, PresentationRequest, SqliteTransaction, SqliteReadView, DiscoveryScope, PageRequest, Page, NewPost } from '../contracts.js';
import { transactionRequest, page, sized } from '../paging.js';
import { socialTime } from '../clock.js';
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
export class Social extends Service {
    static Config = Config;
    static inject = ['actions', 'information', 'actors', 'socialRepository', 'slots'];
    private config: ReturnType<typeof Config>;
    constructor(ctx: Context, input = {}) {
        super(ctx, 'social');
        this.config = Config(input);
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
        this.installInformation();
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
                }, required: ['postId', 'content'], execute: async (input, context, tx) => this.publish(input, context, tx, 'reply')
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
                }, required: ['postId'], execute: async (input, context, tx) => this.publish(input, context, tx, 'repost')
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
                }, required: ['postId', 'kind'], execute: (input, context, tx) => repository.react(tx, {
                    id: context.operationId, actorId: context.actorId, postId: input.postId, kind: input.kind, time: socialTime(context.logicalTime), remove: input.remove
                })
            },
            follow: {
                properties: {
                    actorId: {
                        type: 'string'
                    }
                }, required: ['actorId'], execute: (input, context, tx) => repository.follow(tx, context.actorId, input.actorId, socialTime(context.logicalTime), context.operationId)
            },
            unfollow: {
                properties: {
                    actorId: {
                        type: 'string'
                    }
                }, required: ['actorId'], execute: (input, context, tx) => repository.follow(tx, context.actorId, input.actorId, socialTime(context.logicalTime), context.operationId, false)
            },
            preferences: {
                properties: {
                    value: {}
                }, required: ['value'], execute: async (input, context, tx) => {
                    await repository.setPreferences(tx, context.actorId, input.value);
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
                }, required: ['ids'], execute: async (input, context, tx) => {
                    await repository.consumeNotifications(tx, context.actorId, input.ids);
                    return {
                        consumed: input.ids
                    };
                }
            },
            present: {
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
                }, outputSchema, effect: 'world', available, execute: async (input, context) => {
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
        for (const id of ['feed', 'profile', 'network', 'notifications', 'posts', 'trending'])
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
            return await this.ctx.socialRepository.publish(tx, this.newPost(input, context, kind, content), recipients);
        }
        finally {
            await selection.release();
        }
    }
}
export default Social;
