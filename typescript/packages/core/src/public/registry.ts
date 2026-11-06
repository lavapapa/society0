/** 声明槽：table/doc 元数据在 generation 边界内可替换，声明本身不写库。 */
import { slotKey } from '@society0/host/slots';
import type { Context } from '@society0/cordis';
import type { Disposer } from '../contracts/common.js';
import type { PublicAudienceProvider, PublicDocDecl, PublicRegistry, PublicTableDecl } from '../contracts/public.js';
import { validateTableDecl } from './canonical.js';

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
    readonly tables = new Map<string, PublicTableDecl>();
    readonly docs = new Map<string, PublicDocDecl>();
    readonly audiences = new Map<string, PublicAudienceProvider>();
    constructor(private ctx: Context) {
        ctx.slots.declare({ key: tableSlot, strategy: 'keyed', required: false, validate: value => validateTableDecl(value as PublicTableDecl) });
        ctx.slots.declare({ key: docSlot, strategy: 'keyed', required: false, validate: value => validateDoc(value as PublicDocDecl) });
        ctx.slots.declare({ key: audienceSlot, strategy: 'keyed', required: false, validate: value => validateAudience(value as PublicAudienceProvider) });
    }
    table(decl: PublicTableDecl): Disposer {
        validateTableDecl(decl);
        if (this.tables.has(decl.path))
            throw Object.assign(new Error('PUBLIC_TABLE_EXISTS: ' + decl.path), { code: 'PUBLIC_TABLE_EXISTS' });
        const withdraw = this.ctx.slots.contribute(tableSlot, { id: decl.path, value: decl });
        this.tables.set(decl.path, decl);
        return () => {
            this.tables.delete(decl.path);
            return withdraw();
        };
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
