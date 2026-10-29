import type { Context } from '@society0/host';
import { slotKey } from '@society0/host/slots';
export function registerNamed<T>(ctx: Context, prefix: string, id: string, value: T, validate: (value: unknown) => T) {
    const key = slotKey<T>(prefix + '/' + id);
    const undeclare = ctx.slots.declare({
        key, strategy: 'single', required: true, validate
    });
    let remove: () => Promise<void> | void;
    try {
        remove = ctx.slots.contribute(key, {
            id, value
        });
    }
    catch (error) {
        void undeclare();
        throw error;
    }
    return ctx.effect(() => async () => {
        await Promise.all([remove(), undeclare()]);
    });
}
export async function acquireNamed<T>(ctx: Context, prefix: string, id: string, generation = 'fixed') {
    const selection = await ctx.slots.acquire(slotKey<T>(prefix + '/' + id), {
        generation
    });
    return {
        value: selection.items[0]!.value, release: () => selection.release()
    };
}
export function sourceVersion(source: Pick<import('./contracts.js').RecommendationSource, 'id' | 'version' | 'dependencies'>): import('./contracts.js').DomainVersion {
    return {
        owner: 'recommendation-source/' + source.id, version: JSON.stringify({
            version: source.version, dependencies: [...source.dependencies].sort()
        })
    };
}
