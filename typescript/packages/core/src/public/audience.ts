/** 受众提供者：每 actor/moment 解析一次，结果用于可见性判断与缓存键。 */
import { compareCodePoints } from '@society0/host/order';
import type { Audience, PublicAudienceProvider } from '../contracts/public.js';
import type { SqliteReadView } from '../sqlite/contracts.js';

export class AudienceResolver {
    private readonly cache = new Map<string, readonly Audience[]>();
    constructor(private readonly providers: () => readonly PublicAudienceProvider[]) { }
    get size() { return this.cache.size; }
    clear() { this.cache.clear(); }
    async resolve(scope: { readonly actorId: string; readonly momentId: string }, view: SqliteReadView): Promise<readonly Audience[]> {
        const key = scope.actorId + '\u0000' + scope.momentId;
        const cached = this.cache.get(key);
        if (cached)
            return cached;
        const set = new Set<Audience>(['all']);
        for (const provider of this.providers())
            for (const audience of await provider.audiences(scope, view))
                set.add(audience);
        const value = [...set].sort(compareCodePoints);
        this.cache.set(key, value);
        return value;
    }
}
export function intersects(file: readonly Audience[], agent: readonly Audience[]): boolean {
    return file.some(audience => agent.includes(audience));
}
export function effective(file: readonly Audience[], agent: readonly Audience[]): readonly Audience[] {
    if (file.length === 1 && file[0] === 'all' && agent.includes('all'))
        return file;
    return file.filter(audience => agent.includes(audience));
}
