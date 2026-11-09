/** 声明槽：table/doc 元数据在 generation 边界内可替换，声明本身不写库。 */
import { slotKey } from '@society0/host/slots';
import type { Context } from '@society0/cordis';
import type { Disposer } from '../contracts/common.js';
import type { PublicAudienceProvider, PublicDocDecl, PublicRegistry, PublicTableDecl } from '../contracts/public.js';
import { isFileTable, tableKey, validateTableDecl } from './canonical.js';

export const tableSlot = slotKey<PublicTableDecl>('society/public-table');
export const docSlot = slotKey<PublicDocDecl>('society/public-doc');
export const audienceSlot = slotKey<PublicAudienceProvider>('society/public-audience');

function validateAudience(provider: PublicAudienceProvider): PublicAudienceProvider {
    if (typeof provider?.id !== 'string' || !provider.id || typeof provider.audiences !== 'function')
        throw Object.assign(new Error('PUBLIC_AUDIENCE_DECL: ' + String(provider?.id)), { code: 'PUBLIC_AUDIENCE_DECL' });
    return provider;
}
function validateDoc(decl: PublicDocDecl): PublicDocDecl {
    if (typeof decl?.path !== 'string' || !decl.path.startsWith('/') || decl.path.endsWith('/'))
        throw Object.assign(new Error('PUBLIC_DOC_PATH: ' + String(decl?.path)), { code: 'PUBLIC_DOC_PATH' });
    if (typeof decl.title !== 'string' || typeof decl.media !== 'string')
        throw Object.assign(new Error('PUBLIC_DOC_DECL'), { code: 'PUBLIC_DOC_DECL' });
    return decl;
}
export class Registry implements PublicRegistry {
    /** 键是完整文件路径（单文件 table）或目录路径（分片 table）。 */
    readonly tables = new Map<string, PublicTableDecl>();
    readonly docs = new Map<string, PublicDocDecl>();
    readonly audiences = new Map<string, PublicAudienceProvider>();
    private readonly kinds = new Map<string, 'file' | 'directory'>();
    private readonly byPath = new Map<string, PublicTableDecl[]>();
    constructor(private ctx: Context) {
        ctx.slots.declare({ key: tableSlot, strategy: 'keyed', required: false, validate: value => validateTableDecl(value as PublicTableDecl) });
        ctx.slots.declare({ key: docSlot, strategy: 'keyed', required: false, validate: value => validateDoc(value as PublicDocDecl) });
        ctx.slots.declare({ key: audienceSlot, strategy: 'keyed', required: false, validate: value => validateAudience(value as PublicAudienceProvider) });
    }
    private reject(key: string, kind: 'file' | 'directory'): void {
        for (const [existingKey, existingKind] of this.kinds) {
            if (key === existingKey) {
                const detail = `${key} (${existingKind} vs ${kind})`;
                if (existingKind !== kind)
                    throw Object.assign(new Error('PUBLIC_TABLE_CONFLICT: ' + detail), { code: 'PUBLIC_TABLE_CONFLICT' });
                throw Object.assign(new Error('PUBLIC_TABLE_EXISTS: ' + key), { code: 'PUBLIC_TABLE_EXISTS' });
            }
            if (kind === 'file' && existingKind === 'directory' && key.startsWith(existingKey + '/'))
                throw Object.assign(new Error(`PUBLIC_TABLE_CONFLICT: ${key} is inside directory table ${existingKey}`), { code: 'PUBLIC_TABLE_CONFLICT' });
            if (kind === 'directory' && existingKind === 'file' && existingKey.startsWith(key + '/'))
                throw Object.assign(new Error(`PUBLIC_TABLE_CONFLICT: ${existingKey} is inside directory table ${key}`), { code: 'PUBLIC_TABLE_CONFLICT' });
        }
    }
    table(decl: PublicTableDecl): Disposer {
        validateTableDecl(decl);
        const key = tableKey(decl), kind = isFileTable(decl) ? 'file' : 'directory';
        this.reject(key, kind);
        const withdraw = this.ctx.slots.contribute(tableSlot, { id: key, value: decl });
        this.tables.set(key, decl);
        this.kinds.set(key, kind);
        const siblings = this.byPath.get(decl.path);
        if (siblings)
            siblings.push(decl);
        else
            this.byPath.set(decl.path, [decl]);
        return () => {
            this.tables.delete(key);
            this.kinds.delete(key);
            const list = this.byPath.get(decl.path);
            if (list) {
                const index = list.indexOf(decl);
                if (index >= 0)
                    list.splice(index, 1);
                if (!list.length)
                    this.byPath.delete(decl.path);
            }
            return withdraw();
        };
    }
    /** 目录路径对应的唯一声明；同一目录下有多张文件表时返回 undefined。 */
    tableForDirectory(path: string): PublicTableDecl | undefined {
        const matches = this.byPath.get(path);
        return matches?.length === 1 ? matches[0] : undefined;
    }
    doc(decl: PublicDocDecl): Disposer {
        validateDoc(decl);
        if (this.docs.has(decl.path))
            throw Object.assign(new Error('PUBLIC_DOC_EXISTS: ' + decl.path), { code: 'PUBLIC_DOC_EXISTS' });
        const withdraw = this.ctx.slots.contribute(docSlot, { id: decl.path, value: decl });
        this.docs.set(decl.path, decl);
        return () => {
            this.docs.delete(decl.path);
            return withdraw();
        };
    }
    audience(provider: PublicAudienceProvider): Disposer {
        validateAudience(provider);
        if (this.audiences.has(provider.id))
            throw Object.assign(new Error('PUBLIC_AUDIENCE_EXISTS: ' + provider.id), { code: 'PUBLIC_AUDIENCE_EXISTS' });
        const withdraw = this.ctx.slots.contribute(audienceSlot, { id: provider.id, value: provider });
        this.audiences.set(provider.id, provider);
        return () => {
            this.audiences.delete(provider.id);
            return withdraw();
        };
    }
}
