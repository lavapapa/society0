import {compareCodePoints} from '@society0/host/order';
import { Context, Service } from '@society0/host';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({
    sources: Schema.array(Schema.string()).default(['social/posts/v1']), cacheBytes: Schema.natural().min(1).default(4194304),
    fullScanUntil: Schema.natural().min(1).default(5000), recentKeepCount: Schema.natural().min(1).default(1000),
    topEngagementKeepCount: Schema.natural().min(1).default(500), minLifetimeTicks: Schema.natural().default(50)
});
export const metadata: PluginMetadata = {
    "title": "social-recommendations", "summary": "social-recommendations", "scope": "generation", "hostAbi": "society0/host/1", "configVersion": 1, "configPolicy": [{
            "path": "/sources", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/cacheBytes", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/fullScanUntil", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/recentKeepCount", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/topEngagementKeepCount", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/minLifetimeTicks", "mode": "new-run", "affectsResults": true
        }]
};
export const api: ApiEntry = {
    "exportPath": "./social-recommendations", "provides": [{
            "key": "recommendations", "protocol": "recommendations", "major": 1, "capabilities": []
        }, {
            "key": "recommendationSources", "protocol": "recommendationSources", "major": 1, "capabilities": []
        }], "requires": [{
            "service": {
                "key": "socialRepository", "protocol": "socialRepository", "major": 1, "capabilities": []
            }, "optional": false, "phase": "module"
        }, {
            "service": {
                "key": "database", "protocol": "database", "major": 1, "capabilities": []
            }, "optional": false, "phase": "module"
        }, {
            "service": {
                "key": "socialRankings", "protocol": "socialRankings", "major": 1, "capabilities": []
            }, "optional": false, "phase": "module"
        }, {
            "service": {
                "key": "slots", "protocol": "slots", "major": 1, "capabilities": []
            }, "optional": false, "phase": "module"
        }]
};
import { randomUUID } from 'node:crypto';
import '@society0/core/sqlite';
import '../social-repository/index.js';
import { validateRanking } from '../social-ranking/index.js';
import { registerNamed, acquireNamed, sourceVersion } from '../registry.js';
import { after, page, bytes, sized } from '../paging.js';
import { socialTime } from '../clock.js';
import type { RecommendationSource, RecommendationInput, RecommendationPreparation, DiscoveryScope, Invocation, Lease, DomainVersion, Page, PageRequest, Json, SocialReader } from '../contracts.js';
declare module '@society0/host' {
    interface Context {
        recommendations: Recommendations;
        recommendationSources: RecommendationSources;
    }
}
export class RecommendationSources extends Service {
    constructor(ctx: Context) {
        super(ctx, 'recommendationSources');
    }
    register(source: RecommendationSource) {
        const retirement = new AbortController();
        const removeVersions = this.ctx.socialRepository.registerVersionSource([sourceVersion(source).owner, ...source.dependencies], async (reader) => [sourceVersion(source), ...await source.versions(reader)]);
        let remove: () => void | Promise<void>;
        try {
            remove = registerNamed(this.ctx, 'society/recommendation-source', source.id, {
                source, signal: retirement.signal
            }, value => {
                const record = value as {
                    source: RecommendationSource;
                    signal: AbortSignal;
                };
                if (typeof record?.source?.collect !== 'function' || typeof record.source.versions !== 'function')
                    throw Error('RECOMMENDATION_SOURCE');
                return record;
            });
        }
        catch (error) {
            void removeVersions();
            throw error;
        }
        return this.ctx.effect(() => async () => {
            retirement.abort(Error('RECOMMENDATION_SOURCE_RETIRED ' + source.id));
            await Promise.all([remove(), removeVersions()]);
        });
    }
    async acquire(id: string, generation: string) {
        const held = await acquireNamed<{
            source: RecommendationSource;
            signal: AbortSignal;
        }>(this.ctx, 'society/recommendation-source', id, generation);
        return {
            value: held.value.source, signal: held.value.signal, release: () => held.release()
        };
    }
}
type PreparationBounds = {
    maxItems: number;
    maxBytes: number;
};
function preparationEnvelope(input: RecommendationInput) {
    return JSON.stringify({
        id: input.id, runId: input.runId, scope: input.scope, versions: input.versions, items: input.items.map(item => [item.id, item.authorId]), maxBytes: input.maxBytes, sourcePages: input.sourcePages
    });
}
export class Recommendations extends Service {
    static Config = Config;
    static inject = ['socialRepository', 'database', 'socialRankings', 'slots'];
    private config: ReturnType<typeof Config>;
    private selections?: Promise<Lease<RecommendationSource>[]>;
    private generation?: string;
    private active = new Set<Promise<unknown>>();
    private closing = false;
    private cache = new Map<string, {
        input: RecommendationInput;
        bytes: number;
    }>();
    private used = 0;
    readonly metrics = {
        preparations: 0, cacheHits: 0, stale: 0
    };
    constructor(ctx: Context, input = {}) {
        super(ctx, 'recommendations');
        this.config = Config(input);
        const config = this.config;
        new RecommendationSources(ctx);
        const features = async (scope: DiscoveryScope, reader: SocialReader, posts: Page<any>) => {
            const following = new Set(await ctx.socialRepository.adjacent(reader, scope.actorId, 'following'));
            const member = await ctx.socialRepository.requireMember(reader, scope.actorId);
            return {
                ...posts, items: posts.items.map((post: any) => ({
                    id: post.id, authorId: post.authorId, features: {
                        content: post.content, tags: post.tags, createdTime: post.createdTime, textVersion: post.textVersion, likes: post.likes, replies: post.replies, reposts: post.reposts, metadata: post.metadata, following: following.has(post.authorId), preferences: member.preferences
                    }
                }))
            };
        };
        ctx.recommendationSources.register({
            id: 'social/posts/v1', version: 1, dependencies: ['social/content', 'social/relations', 'social/preferences'], versions: reader => ctx.socialRepository.versions(reader), collect: async (scope, reader, request) => features(scope, reader, await ctx.socialRepository.posts(reader, request, {
                excludeAuthor: scope.actorId
            }))
        });
        ctx.recommendationSources.register({
            id: 'social/active-pool/v1', version: 1, dependencies: ['social/content', 'social/relations', 'social/preferences'], versions: reader => ctx.socialRepository.versions(reader), collect: async (scope, reader, request) => {
                const time = socialTime(scope.query && typeof scope.query === 'object' && 'time' in scope.query ? (scope.query as any).time : undefined);
                return features(scope, reader, await ctx.socialRepository.activePool(reader, request, time, {
                    fullScanUntil: config.fullScanUntil, recentKeepCount: config.recentKeepCount,
                    topEngagementKeepCount: config.topEngagementKeepCount, minLifetimeTicks: config.minLifetimeTicks
                }));
            }
        });
        ctx.effect(() => async () => {
            this.closing = true;
            await Promise.allSettled([...this.active]);
            if (this.selections)
                for (const held of await this.selections.catch(() => []))
                    await held.release();
            this.cache.clear();
            this.used = 0;
        });
    }
    private async sources(generation: string) {
        if (this.generation && this.generation !== generation)
            throw Error('RECOMMENDATION_GENERATION');
        this.generation = generation;
        return this.selections ??= (async () => {
            const held: Lease<RecommendationSource>[] = [];
            try {
                for (const id of this.config.sources) {
                    const lease = await this.ctx.recommendationSources.acquire(id, generation);
                    held.push(lease);
                    const retire = () => {
                        this.closing = true;
                        void Promise.allSettled([...this.active]).then(async () => {
                            for (const item of held)
                                await item.release();
                        });
                    };
                    lease.signal.addEventListener('abort', retire, {
                        once: true
                    });
                    if (lease.signal.aborted)
                        retire();
                }
                return held;
            }
            catch (error) {
                for (const lease of held)
                    await lease.release();
                throw error;
            }
        })();
    }
    prepare(scope: DiscoveryScope, bounds: {
        maxItems: number;
        maxBytes: number;
    }, context: Invocation): Promise<RecommendationPreparation> {
        if (this.closing)
            throw Error('RECOMMENDATION_CLOSED');
        const task = this.prepareOwned(scope, bounds, context);
        this.active.add(task);
        void task.finally(() => this.active.delete(task)).catch(() => {
        });
        return task;
    }
    private async sourceVersions(held: readonly Lease<RecommendationSource>[], reader: SocialReader) {
        const owners = new Map<string, string>();
        for (const source of held)
            for (const item of [sourceVersion(source.value), ...await source.value.versions(reader)]) {
                const previous = owners.get(item.owner);
                if (previous !== undefined && previous !== item.version)
                    throw Error('RECOMMENDATION_VERSION_CONFLICT ' + item.owner);
                owners.set(item.owner, item.version);
            }
        return [...owners].map(([owner, version]) => ({
            owner, version
        })).sort((a, b) => compareCodePoints(a.owner, b.owner));
    }
    private async collectInput(held: readonly Lease<RecommendationSource>[], scope: DiscoveryScope, view: import('../contracts.js').SqliteReadView, bounds: PreparationBounds) {
        const items: RecommendationInput['items'][number][] = [], sourcePages: {
            sourceId: string;
            total: string;
            next?: string;
        }[] = [], seen = new Set<string>();
        for (const source of held) {
            const result = await source.value.collect(scope, view, {
                maxItems: bounds.maxItems, maxResponseBytes: bounds.maxBytes
            });
            sourcePages.push({
                sourceId: source.value.id, total: result.total, ...(result.next ? {
                    next: result.next
                } : {})
            });
            for (const item of result.items) {
                if (seen.has(item.id))
                    throw Error('RECOMMENDATION_DUPLICATE_ITEM');
                seen.add(item.id);
                items.push(item);
                if (items.length > bounds.maxItems || bytes(items) > bounds.maxBytes)
                    throw Error('RECOMMENDATION_ITEM_BUDGET');
            }
        }
        return {
            items, sourcePages
        };
    }
    private async capture(held: readonly Lease<RecommendationSource>[], scope: DiscoveryScope, bounds: PreparationBounds, context: Invocation) {
        const view = await this.ctx.database.snapshot({
            maxAgeMs: 30000
        });
        try {
            const versions = await this.sourceVersions(held, view), key = JSON.stringify({
                scope, sources: this.config.sources, versions, bounds, generation: context.generation
            });
            const cached = this.cache.get(key);
            if (cached) {
                this.metrics.cacheHits++;
                this.cache.delete(key);
                this.cache.set(key, cached);
                return {
                    cached: true, key, input: structuredClone(cached.input)
                };
            }
            const collected = await this.collectInput(held, scope, view, bounds);
            const input: RecommendationInput = {
                id: randomUUID(), runId: view.runId, scope: structuredClone(scope), versions, ...collected, maxBytes: bounds.maxBytes
            };
            if (bytes(input) > bounds.maxBytes)
                throw Error('RECOMMENDATION_INPUT_BYTES');
            return {
                cached: false, key, input
            };
        }
        finally {
            await view.close();
        }
    }
    private async prepareSources(held: readonly Lease<RecommendationSource>[], initial: RecommendationInput, context: Invocation): Promise<RecommendationPreparation> {
        let input = initial;
        const envelope = preparationEnvelope(input);
        for (const source of held) {
            context.signal.throwIfAborted();
            if (!source.value.prepare)
                continue;
            const prepared = await source.value.prepare(input, context);
            if (prepared.status === 'stale')
                return prepared;
            input = prepared.input;
            if (preparationEnvelope(input) !== envelope)
                throw Error('RECOMMENDATION_PREPARE_IDENTITY');
        }
        return {
            status: 'ready', input
        };
    }
    private async changedVersions(held: readonly Lease<RecommendationSource>[], versions: readonly DomainVersion[]) {
        const check = await this.ctx.database.snapshot({
            maxAgeMs: 30000
        });
        try {
            const before = new Map(versions.map(item => [item.owner, item.version])), after = new Map((await this.sourceVersions(held, check)).map(item => [item.owner, item.version]));
            return [...new Set([...before.keys(), ...after.keys()])].filter(owner => before.get(owner) !== after.get(owner));
        }
        finally {
            await check.close();
        }
    }
    private cacheInput(key: string, input: RecommendationInput, bounds: PreparationBounds) {
        const size = bytes(input);
        if (size > bounds.maxBytes)
            throw Error('RECOMMENDATION_INPUT_BYTES');
        if (size > this.config.cacheBytes)
            return;
        const previous = this.cache.get(key);
        if (previous) {
            this.used -= previous.bytes;
            this.cache.delete(key);
        }
        while (this.used + size > this.config.cacheBytes && this.cache.size) {
            const [old, value] = this.cache.entries().next().value!;
            this.cache.delete(old);
            this.used -= value.bytes;
        }
        this.cache.set(key, {
            input: structuredClone(input), bytes: size
        });
        this.used += size;
    }
    private async prepareOwned(scope: DiscoveryScope, bounds: PreparationBounds, context: Invocation): Promise<RecommendationPreparation> {
        context.signal.throwIfAborted();
        const held = await this.sources(context.generation), capture = await this.capture(held, scope, bounds, context);
        if (capture.cached)
            return {
                status: 'ready', input: capture.input
            };
        this.metrics.preparations++;
        const prepared = await this.prepareSources(held, capture.input, context);
        if (prepared.status === 'stale') {
            this.metrics.stale++;
            return prepared;
        }
        context.signal.throwIfAborted();
        const changed = await this.changedVersions(held, capture.input.versions);
        if (changed.length) {
            this.metrics.stale++;
            return {
                status: 'stale', changed
            };
        }
        this.cacheInput(capture.key, prepared.input, bounds);
        return prepared;
    }
    async preview(input: RecommendationInput, strategy: string, request: PageRequest) {
        const selected = await this.ctx.socialRankings.acquire(strategy);
        try {
            const ranked = selected.value.rank(structuredClone(input));
            validateRanking(input, ranked);
            const identity = {
                inputId: input.id, strategy, config: selected.value.configId
            };
            const last = after(request.cursor, identity) || '0';
            const offset = Number(last);
            const rows = ranked.slice(offset, offset + request.maxItems + 1).map((value, i) => ({
                ...value, position: String(offset + i + 1)
            }));
            const result = page(rows, String(ranked.length), input.versions.map(v => v.version).join(':'), request, identity, x => x.position);
            return sized({
                ...result, items: result.items.map(({ position, ...item }) => item), responseBytes: 0
            }, request.maxResponseBytes);
        }
        finally {
            await selected.release();
        }
    }
    async versions(reader: SocialReader) {
        if (!this.selections)
            throw Error('RECOMMENDATION_NOT_PREPARED');
        return this.sourceVersions(await this.selections, reader);
    }
    assertInput(input: RecommendationInput) {
        if (bytes(input) > input.maxBytes || new Set(input.items.map(x => x.id)).size !== input.items.length)
            throw Error('RECOMMENDATION_INPUT');
    }
    diagnostics() {
        return {
            ...this.metrics, cacheBytes: this.used, cacheEntries: this.cache.size, active: this.active.size
        };
    }
}
export default Recommendations;
