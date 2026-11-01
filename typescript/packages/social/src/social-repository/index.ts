import {compareCodePoints} from '@society0/host/order';
import { Context, Service } from '@society0/host';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
import '@society0/core/actors';
import '@society0/core/sqlite';
import '@society0/core/artifacts-local';
import type { SqliteRow } from '@society0/core/sqlite/contracts';
import type { Json, SocialReader, SqliteTransaction, SocialPost, NewPost, PageRequest, PresentationRequest, DomainVersion, SocialReadPort } from '../contracts.js';
import { after, page, bytes } from '../paging.js';
export * from './schema.js';
export const Config = Schema.object({
    maxContentBytes: Schema.natural().min(1).default(65536), maxRecipients: Schema.natural().min(1).default(1024)
});
export const metadata: PluginMetadata = {
    title: 'social-repository', summary: 'Social关系、原文、互动、投递与曝光的原子SQL仓库', scope: 'run', hostAbi: 'society0/host/1', configVersion: 1, configPolicy: ['maxContentBytes', 'maxRecipients'].map(k => ({
        path: '/' + k, mode: 'new-run', affectsResults: true
    }))
};
export const api: ApiEntry = {
    exportPath: './social-repository', provides: [{
            key: 'socialRepository', protocol: 'socialRepository', major: 1, capabilities: []
        }], requires: ['database', 'actors', 'artifacts'].map(key => ({
        service: {
            key, protocol: key, major: 1, capabilities: []
        }, optional: false, phase: 'module'
    }))
};
declare module '@society0/host' {
    interface Context {
        socialRepository: SocialRepository;
    }
}
function post(row: SqliteRow): SocialPost {
    return {
        id: String(row.id), ordinal: String(row.ordinal), authorId: String(row.author_id), content: String(row.content), tags: JSON.parse(String(row.tags)), createdTime: String(row.created_time), kind: row.kind as SocialPost['kind'], parentId: row.parent_id === null ? null : String(row.parent_id), textVersion: String(row.text_version), likes: String(row.likes), replies: String(row.replies), reposts: String(row.reposts), views: String(row.views), metadata: JSON.parse(String(row.metadata))
    };
}
interface VersionSource {
    owners: readonly string[];
    read: (reader: SocialReader) => Promise<readonly DomainVersion[]>;
    users: number;
    done?: () => void;
}
export class SocialRepository extends Service {
    static Config = Config;
    static inject = ['database', 'actors', 'artifacts'];
    private config: ReturnType<typeof Config>;
    private versionSources = new Set<VersionSource>();
    private contentListeners = new Set<(tx: SqliteTransaction, post: SocialPost) => Promise<void>>();
    constructor(ctx: Context, input = {}) {
        super(ctx, 'socialRepository');
        this.config = Config(input);
    }
    onContent(listener: (tx: SqliteTransaction, post: SocialPost) => Promise<void>) {
        this.contentListeners.add(listener);
        return this.ctx.effect(() => () => {
            this.contentListeners.delete(listener);
        });
    }
    async contentChanged(tx: SqliteTransaction, post: SocialPost) {
        for (const listener of [...this.contentListeners])
            await listener(tx, post);
    }
    async revise(tx: SqliteTransaction, id: string, content: string, tags: readonly string[]) {
        const original = await this.get(id, tx);
        if (!original)
            throw Error('SOCIAL_POST_MISSING');
        if (!content.trim() || Buffer.byteLength(content) > this.config.maxContentBytes)
            throw Error('SOCIAL_CONTENT_BUDGET');
        await tx.execute({
            sql: 'UPDATE social_posts SET content=?,tags=?,text_version=text_version+1 WHERE id=?', parameters: [content, JSON.stringify(tags), id]
        });
        await tx.execute({
            sql: 'DELETE FROM social_tags WHERE post=?', parameters: [id]
        });
        for (const tag of new Set(tags))
            await tx.execute({
                sql: 'INSERT INTO social_tags VALUES(?,?)', parameters: [id, tag]
            });
        if (original.kind === 'reply')
            await tx.execute({
                sql: 'UPDATE social_comments SET content=? WHERE id=?', parameters: [content, id]
            });
        await this.bump(tx, 'social/content');
        const result = (await this.get(id, tx))!;
        await this.contentChanged(tx, result);
        return result;
    }
    async bump(tx: SqliteTransaction, ...owners: string[]) {
        for (const owner of new Set(owners))
            await tx.execute({
                sql: 'INSERT INTO social_versions VALUES(?,1) ON CONFLICT(owner) DO UPDATE SET version=version+1', parameters: [owner]
            });
    }
    async versions(reader: SocialReader, owners: readonly string[] = ['social/content', 'social/relations', 'social/preferences']): Promise<DomainVersion[]> {
        const result: DomainVersion[] = [];
        for (const owner of [...new Set(owners)].sort(compareCodePoints)) {
            const [row] = await reader.query({
                sql: 'SELECT version FROM social_versions WHERE owner=?', parameters: [owner]
            }, {
                maxRows: 1, maxBytes: 4096
            });
            result.push({
                owner, version: row ? String(row.version) : '0'
            });
        }
        return result;
    }
    registerVersionSource(owners: readonly string[], read: (reader: SocialReader) => Promise<readonly DomainVersion[]>) {
        const record: VersionSource = {
            owners, read, users: 0
        };
        this.versionSources.add(record);
        return this.ctx.effect(() => async () => {
            this.versionSources.delete(record);
            if (record.users)
                await new Promise<void>(resolve => record.done = resolve);
        });
    }
    private async contributedVersions(reader: SocialReader, owners: ReadonlySet<string>) {
        const records = [...this.versionSources].filter(record => record.owners.some(owner => owners.has(owner)));
        for (const record of records)
            record.users++;
        try {
            const versions: DomainVersion[] = [];
            for (const record of records)
                versions.push(...await record.read(reader));
            return versions;
        }
        finally {
            for (const record of records)
                if (--record.users === 0)
                    record.done?.();
        }
    }
    private async presentationVersions(reader: SocialReader, expected: readonly DomainVersion[]) {
        const known = new Set(['social/members', 'social/content', 'social/relations', 'social/preferences', 'social/notifications', 'social/engagement', 'social/exposure', 'social/semantic']);
        const own = await this.versions(reader, expected.filter(item => known.has(item.owner)).map(item => item.owner));
        return [...own, ...await this.contributedVersions(reader, new Set(expected.map(item => item.owner)))];
    }
    async assertVersions(reader: SocialReader, versions: readonly DomainVersion[]) {
        const current = await this.presentationVersions(reader, versions);
        const changed = versions.filter(expected => !current.some(actual => actual.owner === expected.owner && actual.version === expected.version)).map(item => item.owner);
        if (changed.length)
            throw Error('SOCIAL_STALE ' + changed.join(','));
    }
    async member(reader: SocialReader, id: string) {
        const [row] = await reader.query({
            sql: 'SELECT id,ordinal,preferences FROM social_members WHERE id=?', parameters: [id]
        }, {
            maxRows: 1, maxBytes: this.config.maxContentBytes
        });
        return row ? {
            id: String(row.id), ordinal: String(row.ordinal), preferences: JSON.parse(String(row.preferences)) as Json
        } : undefined;
    }
    async requireMember(reader: SocialReader, id: string) {
        const member = await this.member(reader, id);
        if (!member)
            throw Error('SOCIAL_MEMBER_MISSING ' + id);
        return member;
    }
    async addMember(tx: SqliteTransaction, actor: {
        id: string;
        attributes: Json;
    }) {
        await this.ctx.actors.create(tx, actor);
        await tx.execute({
            sql: "INSERT INTO social_members SELECT ?,coalesce(max(ordinal),0)+1,'{}' FROM social_members", parameters: [actor.id]
        });
        await this.bump(tx, 'social/members');
    }
    async setPreferences(tx: SqliteTransaction, actorId: string, value: Json) {
        if (bytes(value) > this.config.maxContentBytes)
            throw Error('SOCIAL_CONTENT_BUDGET');
        await this.requireMember(tx, actorId);
        await tx.execute({
            sql: 'UPDATE social_members SET preferences=? WHERE id=?', parameters: [JSON.stringify(value), actorId]
        });
        await this.bump(tx, 'social/preferences');
    }
    async notify(tx: SqliteTransaction, input: {
        id: string;
        actorId: string;
        kind: string;
        time: string;
        data: Json;
    }) {
        await tx.execute({
            sql: 'INSERT INTO social_notifications(id,actor,kind,created_time,consumed,data) VALUES(?,?,?,?,0,?)', parameters: [input.id, input.actorId, input.kind, input.time, JSON.stringify(input.data)]
        });
        await this.bump(tx, 'social/notifications');
    }
    async event(tx: SqliteTransaction, id: string, actor: string, kind: string, target: string, time: string, data: Json) {
        await tx.execute({
            sql: 'INSERT INTO social_events(id,actor,kind,target,created_time,data) VALUES(?,?,?,?,?,?)', parameters: [id, actor, kind, target, time, JSON.stringify(data)]
        });
    }
    async follow(tx: SqliteTransaction, actor: string, target: string, time: string, id: string, enabled = true) {
        await this.requireMember(tx, actor);
        await this.requireMember(tx, target);
        if (actor === target)
            throw Error('SOCIAL_SELF_FOLLOW');
        const result = enabled ? await tx.execute({
            sql: 'INSERT OR IGNORE INTO social_edges(follower,followee,created_time) VALUES(?,?,?)', parameters: [actor, target, time]
        }) : await tx.execute({
            sql: 'DELETE FROM social_edges WHERE follower=? AND followee=?', parameters: [actor, target]
        });
        if (!result.changes)
            return {
                changed: false
            };
        await this.bump(tx, 'social/relations');
        await this.event(tx, id, actor, enabled ? 'follow' : 'unfollow', target, time, {
            target
        });
        if (enabled)
            await this.notify(tx, {
                id: JSON.stringify([id, target]), actorId: target, kind: 'follow', time, data: {
                    actorId: actor
                }
            });
        return {
            changed: true
        };
    }
    async adjacent(reader: SocialReader, actor: string, direction: 'following' | 'followers') {
        await this.requireMember(reader, actor);
        const columns = direction === 'following' ? ['followee', 'follower'] : ['follower', 'followee'];
        const rows = await reader.query({
            sql: `SELECT ${columns[0]} AS id FROM social_edges WHERE ${columns[1]}=? ORDER BY ${columns[0]} LIMIT ?`, parameters: [actor, this.config.maxRecipients + 1]
        }, {
            maxRows: this.config.maxRecipients + 1, maxBytes: this.config.maxRecipients * 256 + 4096
        });
        if (rows.length > this.config.maxRecipients)
            throw Error('SOCIAL_PROPAGATION_BUDGET');
        return rows.map(row => String(row.id));
    }
    readPort(reader: SocialReader): SocialReadPort {
        return {
            neighbors: actor => this.adjacent(reader, actor, 'following'), followers: actor => this.adjacent(reader, actor, 'followers')
        };
    }
    async neighbors(actorId: string, reader: SocialReader, request: PageRequest, direction: 'following' | 'followers' = 'following') {
        await this.requireMember(reader, actorId);
        const version = await this.versions(reader, ['social/relations']);
        const identity = {
            kind: 'neighbors', runId: reader.runId, actorId, direction, version
        };
        const last = after(request.cursor, identity) || '0';
        const column = direction === 'following' ? 'follower' : 'followee';
        const [count] = await reader.query({
            sql: `SELECT count(*) AS n FROM social_edges WHERE ${column}=?`, parameters: [actorId]
        }, {
            maxRows: 1, maxBytes: 1024
        });
        const rows = await reader.query({
            sql: `SELECT * FROM social_edges WHERE ${column}=? AND ordinal>? ORDER BY ordinal LIMIT ?`, parameters: [actorId, BigInt(last), request.maxItems + 1]
        }, {
            maxRows: request.maxItems + 1, maxBytes: Math.max(65536, request.maxResponseBytes * 2)
        });
        return page(rows.map(row => ({
            id: String(row.ordinal), from: String(row.follower), to: String(row.followee), createdTime: String(row.created_time)
        })), String(count!.n), reader.revision ?? '0', request, identity, x => x.id);
    }
    async get(id: string, reader: SocialReader) {
        const [row] = await reader.query({
            sql: 'SELECT * FROM social_posts WHERE id=?', parameters: [id]
        }, {
            maxRows: 1, maxBytes: this.config.maxContentBytes + 65536
        });
        return row ? post(row) : undefined;
    }
    private async validateContent(tx: SqliteTransaction, input: NewPost) {
        await this.requireMember(tx, input.authorId);
        if (!input.content.trim() || Buffer.byteLength(input.content) > this.config.maxContentBytes)
            throw Error('SOCIAL_CONTENT_BUDGET');
        if (!/^(0|[1-9][0-9]*)$/.test(input.createdTime))
            throw Error('SOCIAL_TIME');
    }
    private publicationTargets(authorId: string, recipients: readonly string[]) {
        const targets = [...new Set(recipients)].filter(id => id !== authorId);
        if (targets.length > this.config.maxRecipients)
            throw Error('SOCIAL_PROPAGATION_BUDGET');
        return targets;
    }
    private async publicationParent(tx: SqliteTransaction, input: NewPost, kind: SocialPost['kind']) {
        const parent = input.parentId ? await this.get(input.parentId, tx) : undefined;
        if (input.parentId && !parent)
            throw Error('SOCIAL_POST_MISSING ' + input.parentId);
        if (kind !== 'post' && !parent)
            throw Error('SOCIAL_PARENT_REQUIRED');
        return parent;
    }
    private async insertPost(tx: SqliteTransaction, input: NewPost, kind: SocialPost['kind']) {
        await tx.execute({
            sql: 'INSERT INTO social_posts(id,author_id,content,tags,created_time,kind,parent_id,text_version,likes,replies,reposts,views,metadata) VALUES(?,?,?,?,?,?,?,1,0,0,0,0,?)', parameters: [input.id, input.authorId, input.content, JSON.stringify(input.tags ?? []), input.createdTime, kind, input.parentId ?? null, JSON.stringify(input.metadata ?? null)]
        });
        for (const tag of new Set(input.tags ?? []))
            await tx.execute({
                sql: 'INSERT INTO social_tags VALUES(?,?)', parameters: [input.id, tag]
            });
    }
    private async recordInteraction(tx: SqliteTransaction, input: NewPost, kind: SocialPost['kind'], parent: SocialPost | undefined) {
        if (kind === 'reply') {
            await tx.execute({
                sql: 'INSERT INTO social_comments VALUES(?,?,?,?,?)', parameters: [input.id, parent!.id, input.authorId, input.content, input.createdTime]
            });
            await tx.execute({
                sql: 'UPDATE social_posts SET replies=replies+1 WHERE id=?', parameters: [parent!.id]
            });
        }
        if (kind === 'repost')
            await tx.execute({
                sql: 'UPDATE social_posts SET reposts=reposts+1 WHERE id=?', parameters: [parent!.id]
            });
    }
    private async deliverPost(tx: SqliteTransaction, input: NewPost, kind: SocialPost['kind'], targets: readonly string[]) {
        await this.event(tx, input.id, input.authorId, kind, input.id, input.createdTime, {
            parentId: input.parentId ?? null, recipients: [...targets]
        });
        for (const recipient of targets) {
            await tx.execute({
                sql: 'INSERT INTO social_deliveries(event_id,post,recipient,sender,created_time) VALUES(?,?,?,?,?)', parameters: [input.id, input.id, recipient, input.authorId, input.createdTime]
            });
            await this.notify(tx, {
                id: JSON.stringify([input.id, recipient]), actorId: recipient, kind, time: input.createdTime, data: {
                    postId: input.id, authorId: input.authorId
                }
            });
        }
    }
    private async notifyParent(tx: SqliteTransaction, input: NewPost, kind: SocialPost['kind'], targets: readonly string[], parent: SocialPost | undefined) {
        if (parent && parent.authorId !== input.authorId && !targets.includes(parent.authorId))
            await this.notify(tx, {
                id: JSON.stringify([input.id, parent.authorId]), actorId: parent.authorId, kind, time: input.createdTime, data: {
                    postId: input.id, parentId: parent.id, authorId: input.authorId
                }
            });
    }
    async publish(tx: SqliteTransaction, input: NewPost, recipients: readonly string[]) {
        await this.validateContent(tx, input);
        const kind = input.kind ?? 'post', targets = this.publicationTargets(input.authorId, recipients), parent = await this.publicationParent(tx, input, kind);
        await this.insertPost(tx, input, kind);
        await this.recordInteraction(tx, input, kind, parent);
        await this.deliverPost(tx, input, kind, targets);
        await this.notifyParent(tx, input, kind, targets, parent);
        await this.bump(tx, 'social/content', ...(parent ? ['social/engagement'] : []));
        const result = (await this.get(input.id, tx))!;
        await this.contentChanged(tx, result);
        return result;
    }
    async react(tx: SqliteTransaction, input: {
        id: string;
        actorId: string;
        postId: string;
        kind: string;
        time: string;
        remove?: boolean;
    }) {
        await this.requireMember(tx, input.actorId);
        const target = await this.get(input.postId, tx);
        if (!target)
            throw Error('SOCIAL_POST_MISSING');
        const result = input.remove ? await tx.execute({
            sql: 'DELETE FROM social_reactions WHERE post=? AND actor=? AND kind=?', parameters: [input.postId, input.actorId, input.kind]
        }) : await tx.execute({
            sql: 'INSERT OR IGNORE INTO social_reactions(post,actor,kind,created_time) VALUES(?,?,?,?)', parameters: [input.postId, input.actorId, input.kind, input.time]
        });
        if (!result.changes)
            return {
                changed: false
            };
        if (input.kind === 'like')
            await tx.execute({
                sql: 'UPDATE social_posts SET likes=likes+? WHERE id=?', parameters: [input.remove ? -1 : 1, input.postId]
            });
        await this.event(tx, input.id, input.actorId, input.remove ? 'unreact' : 'react', input.postId, input.time, {
            kind: input.kind
        });
        if (!input.remove && target.authorId !== input.actorId)
            await this.notify(tx, {
                id: JSON.stringify([input.id, target.authorId]), actorId: target.authorId, kind: 'reaction', time: input.time, data: {
                    postId: input.postId, actorId: input.actorId, reaction: input.kind
                }
            });
        await this.bump(tx, 'social/content', 'social/engagement');
        return {
            changed: true
        };
    }
    private postFilter(filter: {
        actorId?: string;
        id?: string;
        authorId?: string;
        excludeAuthor?: string;
        parentId?: string;
        tag?: string;
        through?: string;
    }) {
        const conditions: string[] = [], parameters: (string | bigint | number)[] = [];
        if (filter.actorId) {
            conditions.push('(p.author_id=? OR EXISTS(SELECT 1 FROM social_deliveries d WHERE d.post=p.id AND d.recipient=?))');
            parameters.push(filter.actorId, filter.actorId);
        }
        if (filter.id) {
            conditions.push('p.id=?');
            parameters.push(filter.id);
        }
        if (filter.authorId) {
            conditions.push('p.author_id=?');
            parameters.push(filter.authorId);
        }
        if (filter.excludeAuthor) {
            conditions.push('p.author_id<>?');
            parameters.push(filter.excludeAuthor);
        }
        if (filter.parentId) {
            conditions.push('p.parent_id=?');
            parameters.push(filter.parentId);
        }
        if (filter.tag) {
            conditions.push('EXISTS(SELECT 1 FROM social_tags t WHERE t.post=p.id AND t.tag=?)');
            parameters.push(filter.tag);
        }
        return { conditions, parameters };
    }

    async posts(reader: SocialReader, request: PageRequest, filter: {
        actorId?: string;
        id?: string;
        authorId?: string;
        excludeAuthor?: string;
        parentId?: string;
        tag?: string;
        through?: string;
    } = {}) {
        const { conditions, parameters } = this.postFilter(filter);
        const where = conditions.length ? conditions.join(' AND ') : '1';
        const identity = {
            kind: 'posts', runId: reader.runId, filter, versions: await this.versions(reader, ['social/content', 'social/relations', 'social/exposure'])
        };
        const last = after(request.cursor, identity) || '0';
        const [count] = await reader.query({
            sql: `SELECT count(*) AS n FROM social_posts p WHERE ${where}`, parameters
        }, {
            maxRows: 1, maxBytes: 1024
        });
        const rows = await reader.query({
            sql: `SELECT p.* FROM social_posts p WHERE ${where} AND p.ordinal>? ORDER BY p.ordinal LIMIT ?`, parameters: [...parameters, BigInt(last), request.maxItems + 1]
        }, {
            maxRows: request.maxItems + 1, maxBytes: Math.max(this.config.maxContentBytes + 65536, request.maxResponseBytes * 2)
        });
        return page(rows.map(post), String(count!.n), reader.revision ?? '0', request, identity, x => x.ordinal);
    }
    async feed(actorId: string, reader: SocialReader, request: PageRequest) {
        await this.requireMember(reader, actorId);
        return this.posts(reader, request, {
            actorId
        });
    }
    async profile(actorId: string, reader: SocialReader) {
        const member = await this.requireMember(reader, actorId), actor = await this.ctx.actors.get(actorId, reader);
        const [counts] = await reader.query({
            sql: 'SELECT (SELECT count(*) FROM social_edges WHERE follower=?) AS following,(SELECT count(*) FROM social_edges WHERE followee=?) AS followers,(SELECT count(*) FROM social_posts WHERE author_id=?) AS posts,(SELECT count(*) FROM social_notifications WHERE actor=? AND consumed=0) AS unread', parameters: [actorId, actorId, actorId, actorId]
        }, {
            maxRows: 1, maxBytes: 4096
        });
        return {
            ...member, attributes: actor!.attributes, following: String(counts!.following), followers: String(counts!.followers), posts: String(counts!.posts), unread: String(counts!.unread)
        };
    }
    async notifications(actorId: string, reader: SocialReader, request: PageRequest, unread = false) {
        await this.requireMember(reader, actorId);
        const identity = {
            kind: 'notifications', actorId, runId: reader.runId, unread, versions: await this.versions(reader, ['social/notifications'])
        };
        const last = after(request.cursor, identity) || '0';
        const where = unread ? 'actor=? AND consumed=0' : 'actor=?';
        const [count] = await reader.query({
            sql: `SELECT count(*) AS n FROM social_notifications WHERE ${where}`, parameters: [actorId]
        }, {
            maxRows: 1, maxBytes: 1024
        });
        const rows = await reader.query({
            sql: `SELECT * FROM social_notifications WHERE ${where} AND ordinal>? ORDER BY ordinal LIMIT ?`, parameters: [actorId, BigInt(last), request.maxItems + 1]
        }, {
            maxRows: request.maxItems + 1, maxBytes: Math.max(65536, request.maxResponseBytes * 2)
        });
        return page(rows.map(row => ({
            id: String(row.id), ordinal: String(row.ordinal), actorId: String(row.actor), kind: String(row.kind), createdTime: String(row.created_time), consumed: !!row.consumed, data: JSON.parse(String(row.data)) as Json
        })), String(count!.n), reader.revision ?? '0', request, identity, x => x.ordinal);
    }
    async consumeNotifications(tx: SqliteTransaction, actorId: string, ids: readonly string[]) {
        for (const id of new Set(ids)) {
            const result = await tx.execute({
                sql: 'UPDATE social_notifications SET consumed=1 WHERE actor=? AND id=? AND consumed=0', parameters: [actorId, id]
            });
            if (result.changes)
                await this.bump(tx, 'social/notifications');
        }
    }
    async present(tx: SqliteTransaction, request: PresentationRequest) {
        if (new Set(request.itemIds).size !== request.itemIds.length)
            throw Error('SOCIAL_PRESENTATION_DUPLICATE_ITEM');
        const encoded = JSON.stringify({
            presentationId: request.presentationId, actorId: request.actorId, momentId: request.momentId, inputId: request.inputId, versions: [...request.versions].sort((a, b) => compareCodePoints(a.owner, b.owner)), itemIds: request.itemIds
        });
        const [previous] = await tx.query({
            sql: 'SELECT request FROM social_presentations WHERE id=?', parameters: [request.presentationId]
        }, {
            maxRows: 1, maxBytes: 1048576
        });
        if (previous) {
            if (previous.request !== encoded)
                throw Error('SOCIAL_PRESENTATION_CONFLICT');
            return;
        }
        await this.requireMember(tx, request.actorId);
        await this.assertVersions(tx, request.versions);
        await tx.execute({
            sql: 'INSERT INTO social_presentations VALUES(?,?,?,?,?)', parameters: [request.presentationId, request.actorId, request.momentId, request.inputId, encoded]
        });
        for (const id of new Set(request.itemIds)) {
            await tx.execute({
                sql: 'INSERT INTO social_exposures VALUES(?,?,?)', parameters: [request.presentationId, id, request.actorId]
            });
            await tx.execute({
                sql: 'UPDATE social_posts SET views=views+1 WHERE id=?', parameters: [id]
            });
        }
        await tx.execute({
            sql: 'INSERT INTO social_recommended VALUES(?,?,?) ON CONFLICT(actor) DO UPDATE SET input_id=excluded.input_id,ids=excluded.ids', parameters: [request.actorId, request.inputId, JSON.stringify(request.itemIds)]
        });
        await this.bump(tx, 'social/exposure');
    }
    async trending(reader: SocialReader, request: PageRequest, input: {
        time: string;
        window: string;
    }) {
        const now = BigInt(input.time), since = now - BigInt(input.window);
        if (now < 0n || BigInt(input.window) < 0n)
            throw Error('SOCIAL_TIME');
        const clauses = ['(length(created_time)<? OR (length(created_time)=? AND created_time<=?))'];
        const n = String(now), parameters: (string | number | bigint)[] = [n.length, n.length, n];
        if (since >= 0n) {
            const s = String(since);
            clauses.push('(length(created_time)>? OR (length(created_time)=? AND created_time>?))');
            parameters.push(s.length, s.length, s);
        }
        const where = clauses.join(' AND '), heat = '(likes+replies+reposts)';
        const identity = {
            kind: 'trending', runId: reader.runId, input, versions: await this.versions(reader, ['social/content', 'social/exposure'])
        };
        const last = after(request.cursor, identity);
        const [count] = await reader.query({
            sql: `SELECT count(*) AS n FROM social_posts WHERE ${where}`, parameters
        }, {
            maxRows: 1, maxBytes: 1024
        });
        let keyset = '';
        if (last) {
            const [score, ordinal] = last.split(':');
            keyset = ` AND (${heat}<? OR (${heat}=? AND ordinal>?))`;
            parameters.push(BigInt(score!), BigInt(score!), BigInt(ordinal!));
        }
        const rows = await reader.query({
            sql: `SELECT *,${heat} AS heat FROM social_posts WHERE ${where}${keyset} ORDER BY ${heat} DESC,ordinal LIMIT ?`, parameters: [...parameters, request.maxItems + 1]
        }, {
            maxRows: request.maxItems + 1, maxBytes: Math.max(this.config.maxContentBytes + 65536, request.maxResponseBytes * 2)
        });
        return page(rows.map(row => ({
            ...post(row), heat: String(row.heat)
        })), String(count!.n), reader.revision ?? '0', request, identity, x => x.heat + ':' + x.ordinal);
    }
}
export default SocialRepository;
