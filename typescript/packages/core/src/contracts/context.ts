import { compareCodePoints } from '@society0/host/order';
import { slotKey } from '@society0/host/slots';
import type { Json } from './common.js';
import type { DeliveryChannel } from './delivery.js';
import { canonicalJson } from './affordance.js';

export const DEFAULT_CONTEXT_BUDGET = {
    header: 1500,
    baseline: 900,
    stateDelta: 900,
    actions: 800,
    percepts: 1200,
    history: 2100,
    output: 512,
    reserve: 280,
    toolResultMax: 800
} as const;
export type ContextSlot = keyof typeof DEFAULT_CONTEXT_BUDGET;

export interface ContextBudget {
    readonly tokens: Readonly<Record<ContextSlot, number>>;
    slot(name: ContextSlot): number;
    bytes(name: ContextSlot): number;
    total(): number;
    estimate(text: string | readonly string[]): number;
}

/** 字节/3.2 的保守估算；预算与硬上限（prompts.maxContextBytes）分开。 */
export function contextBudget(tokens: Partial<Record<ContextSlot, number>> = {}): ContextBudget {
    const merged = { ...DEFAULT_CONTEXT_BUDGET, ...tokens };
    return {
        tokens: merged,
        slot: name => merged[name],
        bytes: name => Math.floor(merged[name] * 3.2),
        total: () => Object.values(merged).reduce((sum, value) => sum + value, 0),
        estimate: text => Math.ceil(Buffer.byteLength(typeof text === 'string' ? text : text.join('\n')) / 3.2)
    };
}

/** 上层插件提供的主体状态快照；driver 负责按线程历史 fold 后只送增量。 */
export interface StateSource {
    readonly id: string;
    readonly budgetSlot?: ContextSlot;
    snapshot(actorId: string, momentId: string): Promise<Json>;
}

export const STATE_SOURCE = slotKey<StateSource>('society/state-source');

interface StateRecord extends Record<string, Json> {
    readonly values: Json;
}
interface StateDelta {
    readonly values: Json;
    readonly replaced: Json;
    readonly removed: readonly string[];
    readonly initial: boolean;
}

function isObject(value: Json): value is Readonly<Record<string, Json>> {
    return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function stateObject(value: Json): Readonly<Record<string, Json>> {
    return isObject(value) ? value : {};
}

function stableEqual(left: Json, right: Json): boolean {
    return canonicalJson(left) === canonicalJson(right);
}

export const stateDeliveryChannel: DeliveryChannel = {
    name: 'state',
    empty: {},
    fold: (state, record) => {
        const base = { ...stateObject(state as Json) };
        const value = record as StateRecord & { removed?: readonly string[] };
        const values = stateObject(value.values ?? record);
        for (const key of value.removed ?? [])
            delete base[key];
        Object.assign(base, values);
        return base as unknown as Json;
    },
    diff: (delivered, valid, deliver) => {
        const previous = stateObject(delivered as Json);
        const next = stateObject((deliver[0] ?? valid[0] ?? {}) as Json);
        const replaced: Record<string, Json> = {};
        const removed: string[] = [];
        for (const [key, value] of Object.entries(next))
            if (!(key in previous) || !stableEqual(previous[key]!, value))
                replaced[key] = value;
        for (const key of Object.keys(previous))
            if (!(key in next))
                removed.push(key);
        return { values: next as unknown as Json, replaced: replaced as unknown as Json, removed, initial: Object.keys(previous).length === 0 } satisfies StateDelta;
    },
    render: delta => {
        const value = delta as StateDelta;
        if (!value || (!Object.keys(value.replaced ?? {}).length && !(value.removed ?? []).length))
            return '';
        const lines: string[] = [];
        if (value.initial) {
            lines.push('当前状态');
            for (const [key, item] of Object.entries(value.values as Readonly<Record<string, Json>>))
                lines.push(`${key} ${canonicalJson(item)}`);
        } else {
            lines.push('状态（变更）');
            for (const [key, item] of Object.entries(value.replaced as Readonly<Record<string, Json>>))
                lines.push(`+ ${key} ${canonicalJson(item)}`);
            for (const key of [...(value.removed ?? [])].sort(compareCodePoints))
                lines.push(`- ${key}`);
        }
        return lines.join('\n');
    }
};

interface PerceptRecord {
    readonly added?: readonly Json[];
    readonly removed?: readonly string[];
}
interface PerceptDelta {
    readonly added: readonly Json[];
    readonly removed: readonly string[];
    readonly initial: boolean;
}

function perceptId(value: Json): string {
    if (isObject(value) && typeof value.id === 'string')
        return value.id;
    return canonicalJson(value);
}

/** 感知按 id 去重；首次全量，之后只送新增与移除。 */
export const perceptsDeliveryChannel: DeliveryChannel = {
    name: 'percepts',
    empty: {},
    fold: (state, record) => {
        const base = new Map(Object.entries(stateObject(state as Json)));
        const value = record as unknown as PerceptRecord;
        for (const id of value.removed ?? [])
            base.delete(id);
        for (const item of value.added ?? [])
            base.set(perceptId(item), item);
        return Object.fromEntries(base) as unknown as Json;
    },
    diff: (delivered, valid, deliver) => {
        const previous = stateObject(delivered as Json);
        const next = new Map<string, Json>();
        for (const item of deliver.length ? deliver : valid)
            next.set(perceptId(item), item);
        const added = [...next].filter(([id, item]) => !(id in previous) || !stableEqual(previous[id]!, item)).map(([, item]) => item);
        const removed = Object.keys(previous).filter(id => !next.has(id));
        return { added, removed, initial: Object.keys(previous).length === 0 } satisfies PerceptDelta;
    },
    render: delta => {
        const value = delta as PerceptDelta;
        if (!value || (!value.added.length && !value.removed.length))
            return '';
        const lines = [value.initial ? '感知' : '感知（变更）'];
        for (const item of value.added)
            lines.push(`+ ${canonicalJson(item)}`);
        for (const id of [...value.removed].sort(compareCodePoints))
            lines.push(`- ${id}`);
        return lines.join('\n');
    }
};
