import { Context, Inject, type Plugin } from '@society0/cordis';
import type { PlannedEntry } from './types.js';
export function entryContext(entry: PlannedEntry, owner: Context, root: Context, labels: Map<string, symbol>): Context {
    let ctx = owner;
    for (const [key, label] of Object.entries(entry.isolate)) {
        let symbol = labels.get(label);
        if (!symbol) {
            symbol = label === `root:${key}` ? (root[Context.isolate][key] ??= Symbol(key)) : Symbol(label);
            labels.set(label, symbol);
        }
        ctx = ctx.isolate(key, symbol);
    }
    for (const [key, value] of Object.entries(entry.intercept))
        ctx = ctx.intercept(key, value);
    return ctx.extend({ societyEntryId: entry.id, societyScope: entry.scope });
}
function originalPlugin(entry: PlannedEntry): Plugin {
    if (entry.group)
        return { name: entry.id, apply() { } };
    if (!entry.binding)
        throw Error(`MODULE_BINDING_MISSING ${entry.id}`);
    return entry.binding.plugin;
}
export function preparedPlugin(entry: PlannedEntry): Plugin {
    const original = originalPlugin(entry);
    const apply = typeof original === 'function' ? undefined : new Proxy(original.apply, {});
    return new Proxy(original, { get(target, key, receiver) {
            if (key === 'apply' && apply)
                return apply;
            if (key === 'Config')
                return undefined;
            if (key === 'inject') {
                const inject = { ...Inject.resolve(original.inject) };
                for (const name of entry.inject)
                    if (!(name in inject))
                        inject[name] = null;
                return inject;
            }
            return Reflect.get(target, key, receiver);
        } });
}
