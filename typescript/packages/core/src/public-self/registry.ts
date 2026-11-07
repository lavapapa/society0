/** SelfProvider 槽：keyed by path，generation 边界内可替换。 */
import { slotKey } from '@society0/host/slots';
import { posix } from 'node:path';
import type { Context } from '@society0/cordis';
import type { Disposer } from '../contracts/common.js';
import type { SelfProvider, SelfRegistry as RegistryContract } from '../contracts/public-self.js';

export const selfProviderSlot = slotKey<SelfProvider>('society/public-self-provider');

function invalid(code: string, detail: string): never {
    throw Object.assign(new Error(code + ': ' + detail), { code });
}
export function validateProvider(provider: SelfProvider): SelfProvider {
    if (typeof provider?.path !== 'string' || !provider.path.startsWith('/'))
        invalid('SELF_PROVIDER_PATH', String(provider?.path));
    if (posix.normalize(provider.path) !== provider.path || provider.path.endsWith('/'))
        invalid('SELF_PROVIDER_PATH', provider.path);
    if (typeof provider.media !== 'string' || !provider.media)
        invalid('SELF_PROVIDER_MEDIA', provider.path);
    if (typeof provider.versions !== 'function' || typeof provider.render !== 'function')
        invalid('SELF_PROVIDER_DECL', provider.path);
    if (provider.visible !== undefined && typeof provider.visible !== 'function')
        invalid('SELF_PROVIDER_DECL', provider.path);
    return provider;
}

export class SelfRegistry implements RegistryContract {
    readonly providers = new Map<string, SelfProvider>();
    constructor(private readonly ctx: Context) {
        ctx.slots.declare({ key: selfProviderSlot, strategy: 'keyed', required: false, validate: value => validateProvider(value as SelfProvider) });
    }
    provide(provider: SelfProvider): Disposer {
        validateProvider(provider);
        if (this.providers.has(provider.path))
            invalid('SELF_PROVIDER_EXISTS', provider.path);
        const withdraw = this.ctx.slots.contribute(selfProviderSlot, { id: provider.path, value: provider });
        this.providers.set(provider.path, provider);
        return () => {
            this.providers.delete(provider.path);
            return withdraw();
        };
    }
}
