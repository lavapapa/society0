import type { Json, Invocation, Lease, Disposer } from '@society0/core/contracts/common';
import type { SqliteReadView, SqliteTransaction } from '@society0/core/sqlite/contracts';
import type { Page, PageRequest, DiscoveryScope, DomainVersion } from '@society0/core/contracts/data';
export type { Json, Invocation, Lease, Disposer, Page, PageRequest, DiscoveryScope, DomainVersion, SqliteReadView, SqliteTransaction };
export type SocialReader = Pick<SqliteReadView, 'query'> & Partial<Pick<SqliteReadView, 'revision' | 'runId'>>;
export interface SocialPost {
    id: string;
    ordinal: string;
    authorId: string;
    content: string;
    tags: readonly string[];
    createdTime: string;
    kind: 'post' | 'reply' | 'repost';
    parentId: string | null;
    textVersion: string;
    likes: string;
    replies: string;
    reposts: string;
    views: string;
    metadata: Json;
}
export interface NewPost {
    id: string;
    authorId: string;
    content: string;
    tags?: readonly string[];
    createdTime: string;
    kind?: SocialPost['kind'];
    parentId?: string | null;
    metadata?: Json;
}
export interface GraphSeed {
    actors: readonly {
        id: string;
        attributes: Json;
    }[];
    edges: readonly {
        from: string;
        to: string;
    }[];
}
export interface TopologyProvider {
    id: string;
    build(input: Json, context: Invocation): Promise<GraphSeed>;
}
export interface SocialReadPort {
    neighbors(actorId: string): Promise<readonly string[]>;
    followers(actorId: string): Promise<readonly string[]>;
}
export interface PropagationStrategy {
    id: string;
    recipients(input: {
        actorId: string;
        content: Json;
    }, view: SocialReadPort): Promise<readonly string[]>;
}
export interface RecommendationItem {
    readonly id: string;
    readonly authorId: string;
    readonly features: Json;
}
export interface RecommendationInput {
    readonly id: string;
    readonly runId: string;
    readonly scope: DiscoveryScope;
    readonly versions: readonly DomainVersion[];
    readonly items: readonly RecommendationItem[];
    readonly maxBytes: number;
    readonly sourcePages?: readonly {
        sourceId: string;
        total: string;
        next?: string;
    }[];
}
export type RecommendationPreparation = {
    readonly status: 'ready';
    readonly input: RecommendationInput;
} | {
    readonly status: 'stale';
    readonly changed: readonly string[];
};
export interface RecommendationSource {
    readonly id: string;
    readonly version: number;
    readonly dependencies: readonly string[];
    versions(snapshot: SocialReader): Promise<readonly DomainVersion[]>;
    collect(scope: DiscoveryScope, snapshot: SqliteReadView, request: PageRequest): Promise<Page<RecommendationItem>>;
    prepare?(input: RecommendationInput, context: Invocation): Promise<RecommendationPreparation>;
}
export interface RecommendationSources {
    register(source: RecommendationSource): Disposer;
    acquire(id: string, generation: string): Promise<Lease<RecommendationSource>>;
}
export interface RankedItem {
    readonly id: string;
    readonly score: number;
    readonly components: Json;
}
export interface SocialRanking {
    readonly id: string;
    readonly source: Readonly<Record<string, Json>>;
    readonly configId: string;
    rank(input: RecommendationInput): readonly RankedItem[];
}
export interface SocialRankings {
    register(strategy: SocialRanking): Disposer;
    acquire(id: string): Promise<Lease<SocialRanking>>;
}
export interface Recommendations {
    prepare(scope: DiscoveryScope, bounds: {
        maxItems: number;
        maxBytes: number;
    }, context: Invocation): Promise<RecommendationPreparation>;
    preview(input: RecommendationInput, strategy: string, request: PageRequest): Promise<Page<RankedItem>>;
}
export interface PresentationRequest {
    presentationId: string;
    inputId: string;
    actorId: string;
    momentId: string;
    versions: readonly DomainVersion[];
    itemIds: readonly string[];
}
export interface MeasurementRecord {
    id: string;
    specId: string;
    kind: 'measurement' | 'interview' | 'ranking';
    actorId: string | null;
    logicalTime: Json;
    value: Json;
    original: Json;
    source: Json;
    required: boolean;
}
