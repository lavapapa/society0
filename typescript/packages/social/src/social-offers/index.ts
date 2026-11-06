import type { DomainVersion } from '@society0/core/contracts/data';
import type { SqliteReader } from '@society0/core/contracts/eligibility';
import type { ActionOffer, OfferProvider, OfferQuery, TargetRef } from '@society0/core/contracts/affordance';
import type { SocialRepository } from '../social-repository/index.js';

/** 目标种类：帖子、主体与通知。 */
export const SOCIAL_POST = 'social/post';
export const SOCIAL_ACTOR = 'social/actor';
export const SOCIAL_NOTIFICATION = 'social/notification';

export interface SocialOfferOptions {
    readonly repository: SocialRepository;
    readonly posts: number;
    readonly actions: readonly string[];
}

interface Row {
    readonly [key: string]: unknown;
}

const rowText = (row: Row, key: string): string => String(row[key]);

/**
 * Social 的 offer provider：把本主体最近呈现过的帖子、最近作者与已关注者
 * 转成 reply/react/repost/follow/unfollow 的带目标可供性。
 *
 * 不加目标过滤时只给出最近 `posts` 条呈现（被收起的条目仍可通过目标过滤
 * 在执行期复查时命中，因此“有效但未展开”的行动仍可调用）。
 */
export class SocialOffers implements OfferProvider {
    readonly id = 'social';
    readonly actions: readonly string[];
    private readonly repository: SocialRepository;
    private readonly posts: number;

    constructor(options: SocialOfferOptions) {
        this.repository = options.repository;
        this.actions = [...options.actions];
        this.posts = options.posts;
    }

    async versions(query: OfferQuery, reader: SqliteReader): Promise<readonly DomainVersion[]> {
        return this.repository.versions(reader, ['social/content', 'social/relations', 'social/exposure', 'social/notifications']);
    }

    private async exposed(reader: SqliteReader, actorId: string, postId: string): Promise<boolean> {
        const [row] = await reader.query({
            sql: 'SELECT 1 AS present FROM social_exposures WHERE actor=? AND post=? LIMIT 1',
            parameters: [actorId, postId],
        }, { maxRows: 1, maxBytes: 1024 });
        return !!row;
    }

    private async author(reader: SqliteReader, postId: string): Promise<string | undefined> {
        const [row] = await reader.query({
            sql: 'SELECT author_id FROM social_posts WHERE id=?',
            parameters: [postId],
        }, { maxRows: 1, maxBytes: 1024 });
        return row ? rowText(row, 'author_id') : undefined;
    }

    private async member(reader: SqliteReader, actorId: string): Promise<boolean> {
        const [row] = await reader.query({
            sql: 'SELECT 1 AS present FROM social_members WHERE id=?',
            parameters: [actorId],
        }, { maxRows: 1, maxBytes: 1024 });
        return !!row;
    }

    private async following(reader: SqliteReader, actorId: string, limit: number): Promise<readonly string[]> {
        const rows = await reader.query({
            sql: 'SELECT followee FROM social_edges WHERE follower=? ORDER BY ordinal DESC LIMIT ?',
            parameters: [actorId, limit],
        }, { maxRows: limit + 1, maxBytes: limit * 128 + 4096 });
        return rows.map(row => rowText(row, 'followee'));
    }

    private async recentPosts(reader: SqliteReader, actorId: string, limit: number): Promise<readonly string[]> {
        const rows = await reader.query({
            sql: 'SELECT post FROM social_exposures WHERE actor=? ORDER BY rowid DESC LIMIT ?',
            parameters: [actorId, limit * 2 + 8],
        }, { maxRows: limit * 2 + 8, maxBytes: (limit * 2 + 8) * 128 + 4096 });
        const seen = new Set<string>();
        const posts: string[] = [];
        for (const row of rows) {
            const post = rowText(row, 'post');
            if (seen.has(post))
                continue;
            seen.add(post);
            posts.push(post);
            if (posts.length >= limit)
                break;
        }
        return posts;
    }

    private async recentAuthors(reader: SqliteReader, actorId: string, limit: number): Promise<readonly string[]> {
        const rows = await reader.query({
            sql: `SELECT p.author_id FROM social_exposures e JOIN social_posts p ON p.id=e.post
                  WHERE e.actor=? AND p.author_id<>? GROUP BY p.author_id ORDER BY max(e.rowid) DESC LIMIT ?`,
            parameters: [actorId, actorId, limit],
        }, { maxRows: limit + 1, maxBytes: limit * 128 + 4096 });
        return rows.map(row => rowText(row, 'author_id'));
    }

    private async unread(reader: SqliteReader, actorId: string, limit: number): Promise<readonly string[]> {
        const rows = await reader.query({
            sql: 'SELECT id FROM social_notifications WHERE actor=? AND consumed=0 ORDER BY ordinal DESC LIMIT ?',
            parameters: [actorId, limit],
        }, { maxRows: limit + 1, maxBytes: limit * 128 + 4096 });
        return rows.map(row => rowText(row, 'id'));
    }

    private postOffers(postId: string): Omit<ActionOffer, 'provider'>[] {
        if (!this.actions.includes('social.reply') && !this.actions.includes('social.react') && !this.actions.includes('social.repost'))
            return [];
        const offers: Omit<ActionOffer, 'provider'>[] = [];
        const target: TargetRef = { kind: SOCIAL_POST, id: postId };
        const prefill = { postId };
        const locked = ['postId'];
        if (this.actions.includes('social.reply'))
            offers.push({ action: 'social.reply', target, prefill, locked, needs: ['content'], priority: 30 });
        if (this.actions.includes('social.react'))
            offers.push({ action: 'social.react', target, prefill, locked, needs: ['kind'], priority: 20 });
        if (this.actions.includes('social.repost'))
            offers.push({ action: 'social.repost', target, prefill, locked, priority: 10 });
        return offers;
    }

    private actorOffers(actorId: string, follow: boolean): Omit<ActionOffer, 'provider'>[] {
        const action = follow ? 'social.follow' : 'social.unfollow';
        if (!this.actions.includes(action))
            return [];
        return [{ action, target: { kind: SOCIAL_ACTOR, id: actorId }, prefill: { actorId }, locked: ['actorId'], priority: follow ? 5 : 6 }];
    }

    private notificationOffers(id: string): Omit<ActionOffer, 'provider'>[] {
        if (!this.actions.includes('social.consumeNotifications'))
            return [];
        return [{ action: 'social.consumeNotifications', target: { kind: SOCIAL_NOTIFICATION, id }, prefill: { ids: [id] }, locked: ['ids'], priority: 40 }];
    }

    private async targeted(reader: SqliteReader, actorId: string, target: TargetRef): Promise<Omit<ActionOffer, 'provider'>[]> {
        if (target.kind === SOCIAL_POST)
            return await this.exposed(reader, actorId, target.id) ? this.postOffers(target.id) : [];
        if (target.kind === SOCIAL_ACTOR) {
            if (target.id === actorId || !await this.member(reader, target.id))
                return [];
            const followed = (await this.following(reader, actorId, this.posts * 4)).includes(target.id);
            return this.actorOffers(target.id, !followed);
        }
        if (target.kind === SOCIAL_NOTIFICATION)
            return await this.exposedNotification(reader, actorId, target.id) ? this.notificationOffers(target.id) : [];
        return [];
    }

    private async exposedNotification(reader: SqliteReader, actorId: string, id: string): Promise<boolean> {
        const [row] = await reader.query({
            sql: 'SELECT 1 AS present FROM social_notifications WHERE actor=? AND id=?',
            parameters: [actorId, id],
        }, { maxRows: 1, maxBytes: 1024 });
        return !!row;
    }

    async offers(query: OfferQuery, reader: SqliteReader): Promise<readonly Omit<ActionOffer, 'provider'>[]> {
        const actorId = query.actorId;
        if (!await this.member(reader, actorId))
            return [];
        if (query.targets?.length) {
            const offers: Omit<ActionOffer, 'provider'>[] = [];
            for (const target of query.targets)
                offers.push(...await this.targeted(reader, actorId, target));
            return offers;
        }
        const offers: Omit<ActionOffer, 'provider'>[] = [];
        const followed = await this.following(reader, actorId, this.posts);
        const posts = await this.recentPosts(reader, actorId, this.posts);
        for (const post of posts)
            offers.push(...this.postOffers(post));
        for (const author of await this.recentAuthors(reader, actorId, this.posts))
            if (!followed.includes(author))
                offers.push(...this.actorOffers(author, true));
        for (const followee of followed)
            offers.push(...this.actorOffers(followee, false));
        for (const id of await this.unread(reader, actorId, this.posts))
            offers.push(...this.notificationOffers(id));
        return offers;
    }
}
