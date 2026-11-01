import { socialTime } from '../clock.js';
import { Context } from '@society0/host';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({
    strategy: Schema.string().default('weighted/v1'),
    previewChars: Schema.union([Schema.const(-1), Schema.natural()]).default(-1)
});
export interface ContentPreview { content: string; truncated?: true; fullRef?: { information: 'social/posts'; input: { id: string } } }
export function previewContent(content: string, id: string, limit: number): ContentPreview {
    if (limit < 0)
        return { content };
    let result = '';
    let count = 0;
    for (const char of content) {
        if (count === limit)
            return { content: result, truncated: true, fullRef: { information: 'social/posts', input: { id } } };
        result += char;
        count++;
    }
    return { content };
}
function previewValue(value: Json, limit: number): Json {
    if (limit < 0 || value === null || typeof value !== 'object' || Array.isArray(value))
        return value;
    const object = value as Record<string, Json>;
    if (typeof object.content !== 'string' || typeof object.id !== 'string')
        return value;
    const preview = previewContent(object.content, object.id, limit);
    return preview.truncated ? { ...object, ...preview } : value;
}
export const metadata: PluginMetadata = {
    "title": "social-perception", "summary": "\u72ec\u7acbactor/thread\u6e38\u6807\u7684Social\u539f\u6587\u4e0e\u63a8\u8350\u611f\u77e5", "scope": "generation", "hostAbi": "society0/host/1", "configVersion": 1, "configPolicy": [{
            "path": "/strategy", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/previewChars", "mode": "new-run", "affectsResults": true
        }]
};
export const api: ApiEntry = {
    "exportPath": "./social-perception", "provides": [], "requires": [{
            "service": {
                "key": "perceptions", "protocol": "perceptions", "major": 1, "capabilities": []
            }, "optional": false, "phase": "module"
        }, {
            "service": {
                "key": "socialRepository", "protocol": "socialRepository", "major": 1, "capabilities": []
            }, "optional": false, "phase": "module"
        }, {
            "service": {
                "key": "recommendations", "protocol": "recommendations", "major": 1, "capabilities": []
            }, "optional": false, "phase": "module"
        }]
};
import '@society0/core/perception';
import type { PerceptionPage, Percept, PerceptionSource, PerceptionScope } from '@society0/core/contracts/perception';
import type { PageBounds } from '@society0/core/contracts/data';
import type { Json, SocialReader } from '../contracts.js';
import '../social-recommendations/index.js';
import '../social-repository/index.js';
import { bytes } from '../paging.js';
function fit(items: readonly {
    cursor: Json;
    percept: Percept;
}[], before: Json, through: Json, bounds: PageBounds): PerceptionPage {
    const selected: Percept[] = [];
    let nextCursor = before;
    for (const item of items.slice(0, bounds.maxItems)) {
        const candidate = {
            items: [...selected, item.percept], nextCursor: item.cursor, through, hasMore: true
        };
        if (bytes(candidate) > bounds.maxResponseBytes) {
            if (!selected.length)
                throw Error('SOCIAL_PERCEPTION_ITEM_BYTES');
            break;
        }
        selected.push(item.percept);
        nextCursor = item.cursor;
    }
    const hasMore = selected.length < items.length;
    return {
        items: selected, nextCursor: selected.length ? nextCursor : through, through, hasMore
    };
}
function inline(id: string, kind: string, value: Json, provenance: Json): Percept {
    return {
        id, kind, material: {
            kind: 'inline', value
        }, provenance
    };
}
function notificationSource(ctx: Context): PerceptionSource {
    return {
        id: 'social/notifications', cursorScope: 'actor', schema: {
            type: 'object'
        }, available: (scope, reader) => ctx.socialRepository.member(reader, scope.actorId).then(Boolean), versions: (_scope, reader) => ctx.socialRepository.versions(reader, ['social/notifications']), async collect(scope, after, through, reader, bounds) {
            const [head] = await reader.query({
                sql: 'SELECT coalesce(max(ordinal),0) AS ordinal FROM social_notifications WHERE actor=?', parameters: [scope.actorId]
            }, {
                maxRows: 1, maxBytes: 2048
            });
            const end = through ?? String(head!.ordinal);
            const rows = await reader.query({
                sql: 'SELECT * FROM social_notifications WHERE actor=? AND ordinal>? AND ordinal<=? AND consumed=0 ORDER BY ordinal LIMIT ?', parameters: [scope.actorId, BigInt(String(after ?? '0')), BigInt(String(end)), bounds.maxItems + 1]
            }, {
                maxRows: bounds.maxItems + 1, maxBytes: Math.min(1048576, Math.max(65536, bounds.maxResponseBytes * 2))
            });
            return fit(rows.map(row => ({
                cursor: String(row.ordinal), percept: inline(String(row.id), 'social.notification', {
                    id: String(row.id), actorId: String(row.actor), kind: String(row.kind), createdTime: String(row.created_time), consumed: !!row.consumed, data: JSON.parse(String(row.data))
                }, {
                    source: 'social/notifications', ordinal: String(row.ordinal), revision: reader.revision
                })
            })), after ?? '0', end, bounds);
        }
    };
}
function feedSource(ctx: Context, previewChars: number): PerceptionSource {
    return {
        id: 'social/feed', cursorScope: 'actor', schema: {
            type: 'object'
        }, available: (scope, reader) => ctx.socialRepository.member(reader, scope.actorId).then(Boolean), versions: (_scope, reader) => ctx.socialRepository.versions(reader, ['social/content', 'social/exposure']), async collect(scope, after, through, reader, bounds) {
            const [head] = await reader.query({
                sql: 'SELECT coalesce(max(ordinal),0) AS ordinal FROM social_deliveries WHERE recipient=?', parameters: [scope.actorId]
            }, {
                maxRows: 1, maxBytes: 2048
            });
            const end = through ?? String(head!.ordinal);
            const rows = await reader.query({
                sql: 'SELECT * FROM social_deliveries WHERE recipient=? AND ordinal>? AND ordinal<=? ORDER BY ordinal LIMIT ?', parameters: [scope.actorId, BigInt(String(after ?? '0')), BigInt(String(end)), bounds.maxItems + 1]
            }, {
                maxRows: bounds.maxItems + 1, maxBytes: Math.min(1048576, Math.max(65536, bounds.maxResponseBytes * 2))
            });
            const items = [];
            for (const row of rows) {
                const post = await ctx.socialRepository.get(String(row.post), reader);
                if (!post)
                    throw Error('SOCIAL_DELIVERY_ORIGINAL_MISSING');
                items.push({
                    cursor: String(row.ordinal), percept: inline(String(row.ordinal), 'social.post', previewValue(post as unknown as Json, previewChars), {
                        source: 'social/feed', eventId: String(row.event_id), sender: String(row.sender), revision: reader.revision
                    })
                });
            }
            return fit(items, after ?? '0', end, bounds);
        }
    };
}
function profileSource(ctx: Context): PerceptionSource {
    return {
        id: 'social/profile', cursorScope: 'thread', schema: {
            type: 'object'
        }, available: (scope, reader) => ctx.socialRepository.member(reader, scope.actorId).then(Boolean), async versions(scope, reader) {
            const profile = await ctx.socialRepository.profile(scope.actorId, reader);
            return [{
                    owner: 'social/profile/' + scope.actorId, version: JSON.stringify(profile)
                }];
        }, async collect(scope, after, _through, reader, bounds) {
            if (after === true)
                return {
                    items: [], nextCursor: true, through: true, hasMore: false
                };
            return fit([{
                    cursor: true, percept: inline(scope.actorId, 'social.profile', await ctx.socialRepository.profile(scope.actorId, reader), {
                        source: 'social/profile', revision: reader.revision
                    })
                }], false, true, bounds);
        }
    };
}
function recommendationScope(scope: PerceptionScope) {
    if (scope.logicalTime === undefined)
        throw Error('SOCIAL_RECOMMENDATION_TIME_REQUIRED');
    return {
        actorId: scope.actorId, momentId: scope.momentId, query: {
            reason: scope.reason, time: socialTime(scope.logicalTime)
        }
    };
}
function recommendationPage(prefix: any, after: any, previewChars: number, bounds: PageBounds) {
    const offset = after === null ? 0 : Number(after);
    const items = prefix.ranked.slice(offset).map((ranked: any, index: number) => ({
        cursor: offset + index + 1, percept: inline(ranked.id, 'social.recommendation', previewValue({
            ...prefix.input.items.find((x: any) => x.id === ranked.id), ranking: ranked
        } as Json, previewChars), {
            source: 'social/recommendations', inputId: prefix.input.id, versions: prefix.input.versions, strategy: prefix.strategy
        })
    }));
    const result = fit(items, offset, prefix, bounds);
    return { ...result, nextCursor: result.hasMore ? result.nextCursor : true, through: result.hasMore ? prefix : true };
}
function recommendationsSource(ctx: Context, strategy: string, previewChars: number): PerceptionSource {
    return {
        id: 'social/recommendations', cursorScope: 'thread', schema: {
            type: 'object'
        }, available: (scope, reader) => ctx.socialRepository.member(reader, scope.actorId).then(Boolean), versions: (_scope, reader) => ctx.recommendations.versions(reader), async prepare(scope, bounds, context) {
            const ready = await ctx.recommendations.prepare(recommendationScope(scope), {
                maxItems: bounds.maxItems, maxBytes: Math.max(1024, Math.floor(bounds.maxResponseBytes / 4))
            }, context);
            if (ready.status === 'stale')
                throw Error('SOCIAL_RECOMMENDATION_STALE ' + ready.changed.join(','));
            const ranked = await ctx.recommendations.preview(ready.input, strategy, {
                maxItems: bounds.maxItems, maxResponseBytes: bounds.maxResponseBytes
            });
            return {
                versions: ready.input.versions, material: {
                    kind: 'inline', value: {
                        input: ready.input as unknown as Json, ranked: ranked.items as unknown as Json, strategy
                    }
                }
            };
        }, async collect(scope, after, through, _reader, bounds, prepared) {
            if (after === true)
                return {
                    items: [], nextCursor: true, through: true, hasMore: false
                };
            if (!prepared || prepared.material.kind !== 'inline')
                throw Error('SOCIAL_RECOMMENDATION_PREPARED');
            return recommendationPage((through ?? prepared.material.value) as any, after, previewChars, bounds);
        }
    };
}
export default {
    Config, inject: ['perceptions', 'socialRepository', 'recommendations'], apply(ctx: Context, input = {}) {
        const config = Config(input);
        for (const source of [profileSource(ctx), notificationSource(ctx), feedSource(ctx, config.previewChars), recommendationsSource(ctx, config.strategy, config.previewChars)])
            ctx.perceptions.register(source);
    }
};
