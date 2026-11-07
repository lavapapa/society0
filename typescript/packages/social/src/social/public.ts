/** social 领域的 /public 推式投影：帖子、trending 与公开 profile 文档。 */
import type { PublicTableDecl } from '@society0/core/contracts/public';
import type { Json } from '../contracts.js';

export const postsDecl: PublicTableDecl = {
    path: '/public/social/posts',
    title: '社交帖子',
    description: '公开帖子、回复与转帖的规范 JSONL；按createdTime分日。',
    schema: {
        type: 'object',
        properties: {
            id: { type: 'string' },
            authorId: { type: 'string' },
            content: { type: 'string' },
            tags: { type: 'array', items: { type: 'string' } },
            createdTime: { type: 'string' },
            kind: { type: 'string' },
            parentId: { type: 'string' }
        }
    },
    key: ['id'],
    sort: [{ field: 'createdTime' }, { field: 'id' }],
    shard: { by: 'createdTime', format: 'day' },
    notify: 'digest'
};

/** 单文件表：文件覆盖为 /public/social/trending.jsonl，避免多一层目录。 */
export const trendingDecl: PublicTableDecl = {
    path: '/public/social',
    title: '社交热度',
    description: '按互动总量排序的公开帖子热度；每次发帖、回复、转帖与互动后更新。',
    schema: {
        type: 'object',
        properties: {
            id: { type: 'string' },
            authorId: { type: 'string' },
            heat: { type: 'integer' },
            createdTime: { type: 'string' }
        }
    },
    key: ['id'],
    sort: [{ field: 'heat', order: 'desc' }, { field: 'createdTime', order: 'desc' }, { field: 'id' }],
    shard: { by: '', format: 'none', file: () => 'trending' },
    notify: 'digest'
};

export const notificationDecl: PublicTableDecl = {
    path: '/self/social/notifications',
    title: '通知',
    description: '本主体通知',
    schema: {
        type: 'object',
        properties: {
            id: { type: 'string' },
            actorId: { type: 'string' },
            kind: { type: 'string' },
            createdTime: { type: 'string' },
            consumed: { type: 'boolean' },
            data: {}
        }
    },
    key: ['id'],
    sort: [{ field: 'createdTime' }, { field: 'id' }],
    shard: { by: '', format: 'none' }
};

export const edgeDecl: PublicTableDecl = {
    path: '/self/social/edge',
    title: '关系',
    description: '关注边',
    schema: {
        type: 'object',
        properties: { id: { type: 'string' }, from: { type: 'string' }, to: { type: 'string' }, createdTime: { type: 'string' } }
    },
    key: ['id'],
    sort: [{ field: 'createdTime' }, { field: 'id' }],
    shard: { by: '', format: 'none' }
};

export interface SocialPublicPost {
    readonly id: string;
    readonly authorId: string;
    readonly content: string;
    readonly tags: readonly string[];
    readonly createdTime: string;
    readonly kind: string;
    readonly parentId: string | null;
    readonly likes: string;
    readonly replies: string;
    readonly reposts: string;
}

export function postRow(post: SocialPublicPost): Json {
    return {
        id: post.id,
        authorId: post.authorId,
        content: post.content,
        tags: [...post.tags],
        createdTime: post.createdTime,
        kind: post.kind,
        parentId: post.parentId
    };
}

export function heatOf(post: { readonly likes: string; readonly replies: string; readonly reposts: string }): number {
    const heat = BigInt(post.likes) + BigInt(post.replies) + BigInt(post.reposts);
    if (heat > BigInt(Number.MAX_SAFE_INTEGER))
        throw Error('SOCIAL_HEAT_BUDGET');
    return Number(heat);
}

export function trendingRow(post: SocialPublicPost): Json {
    return { id: post.id, authorId: post.authorId, heat: heatOf(post), createdTime: post.createdTime };
}

export interface SocialProfile {
    readonly id: string;
    readonly attributes: Json;
    readonly following: string;
    readonly followers: string;
    readonly posts: string;
    readonly unread: string;
    readonly preferences?: Json;
    readonly ordinal?: string;
}

/** 只保留他人可见的字段；preferences、unread、内部 ordinal 都不进入 /public。 */
export function publicProfile(profile: SocialProfile): Json {
    return {
        id: profile.id,
        attributes: profile.attributes,
        following: profile.following,
        followers: profile.followers,
        posts: profile.posts
    };
}
