import type { PluginEntry } from './types.js';
function isEntry(value: unknown): value is PluginEntry {
    return typeof value === 'object' && value !== null && 'id' in value && typeof value.id === 'string' && 'name' in value && typeof value.name === 'string';
}
/** JSON becomes a group child list only after its structural boundary is checked. */
export function groupChildren(entry: PluginEntry): readonly PluginEntry[] {
    const children = entry.config ?? [];
    if (!Array.isArray(children) || !children.every(isEntry))
        throw Error(`GROUP_CHILDREN ${entry.id}`);
    return children;
}
