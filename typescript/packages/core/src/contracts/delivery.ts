import type { Json } from './common.js';

/** 送达通道的固定顺序：状态变更 → 感知 → 可用行动（变更）。 */
export const DELIVERY_CHANNEL_ORDER = ['state', 'percepts', 'actions'] as const;
export type DeliveryChannelName = (typeof DELIVERY_CHANNEL_ORDER)[number];

const ordered: readonly string[] = DELIVERY_CHANNEL_ORDER;
const coded = (code: string) => Object.assign(new Error(code), { code });

/**
 * 一条送达通道。fold/diff/render 都是纯函数：同一 fold 状态、同一组有效项和送达项必须得到
 * 逐字节相同的结果，恢复、重放和分支才成立。通道状态由通道自己定义，框架只负责按名称分桶。
 */
export interface DeliveryChannel {
    readonly name: string;
    /** 结构化副本在 originalProviderFields/details 下的键；缺省与 name 相同（actions 通道用 offers）。 */
    readonly field?: string;
    readonly empty: unknown;
    readonly fold: (state: unknown, record: Json) => unknown;
    readonly diff: (delivered: unknown, valid: readonly Json[], deliver: readonly Json[]) => unknown;
    readonly render: (delta: unknown) => string;
}

/** 对线程历史做一次 fold 后，各通道的已送达状态。 */
export interface DeliveryFold {
    readonly channels: ReadonlyMap<string, unknown>;
}

/** 某通道本次的“当前有效集合”和“本次实际送达集合”（后者已按预算截断）。 */
export interface DeliveryCurrent {
    readonly valid: readonly Json[];
    readonly deliver: readonly Json[];
}

export interface DeliverySegment {
    readonly channel: string;
    readonly delta: unknown;
    readonly text: string;
}

/** 通道名必须属于固定顺序，且不得重复。 */
export function orderDeliveryChannels(channels: readonly DeliveryChannel[]): readonly DeliveryChannel[] {
    const seen = new Set<string>();
    for (const channel of channels) {
        if (!ordered.includes(channel.name))
            throw coded('DELIVERY_CHANNEL_UNKNOWN');
        if (seen.has(channel.name))
            throw coded('DELIVERY_CHANNEL_DUPLICATE');
        seen.add(channel.name);
    }
    return [...channels].sort((left, right) => ordered.indexOf(left.name) - ordered.indexOf(right.name));
}

/** 从一条消息中取出该通道的结构化副本：输入消息在 originalProviderFields，工具结果在 details。 */
function recordOf(message: Json, channel: DeliveryChannel): Json | undefined {
    if (message === null || typeof message !== 'object' || Array.isArray(message))
        return undefined;
    const fields = message as Readonly<Record<string, Json>>;
    for (const source of ['originalProviderFields', 'details']) {
        const container = fields[source];
        if (container === null || typeof container !== 'object' || Array.isArray(container))
            continue;
        const value = (container as Readonly<Record<string, Json>>)[channel.field ?? channel.name];
        if (value !== undefined)
            return value;
    }
    return undefined;
}

/** 按消息顺序折叠单个通道；没有该通道副本的消息不参与。 */
export function foldChannel(channel: DeliveryChannel, messages: readonly Json[]): unknown {
    let state = channel.empty;
    for (const message of messages) {
        const record = recordOf(message, channel);
        if (record !== undefined)
            state = channel.fold(state, record);
    }
    return state;
}

/** 按通道对 originalProviderFields.<channel>（工具结果为 details.<channel>）做 fold。 */
export function foldDelivery(messages: readonly Json[], channels: readonly DeliveryChannel[]): DeliveryFold {
    const folded = new Map<string, unknown>();
    for (const channel of orderDeliveryChannels(channels))
        folded.set(channel.name, foldChannel(channel, messages));
    return { channels: folded };
}

/** 各通道独立计算 diff 并渲染；顺序固定，与传入顺序无关。 */
export function diffDelivery(folded: DeliveryFold, channels: readonly DeliveryChannel[], current: ReadonlyMap<string, DeliveryCurrent>): readonly DeliverySegment[] {
    const segments: DeliverySegment[] = [];
    for (const channel of orderDeliveryChannels(channels)) {
        const entry = current.get(channel.name) ?? { valid: [], deliver: [] };
        const delta = channel.diff(folded.channels.get(channel.name) ?? channel.empty, entry.valid, entry.deliver);
        segments.push({ channel: channel.name, delta, text: channel.render(delta) });
    }
    return segments;
}

/** 按固定顺序把非空通道段拼起来；全部为空时返回空串，调用方不应写入空段。 */
export function renderDelivery(folded: DeliveryFold, channels: readonly DeliveryChannel[], current: ReadonlyMap<string, DeliveryCurrent>): string {
    return diffDelivery(folded, channels, current).map((segment) => segment.text).filter((text) => text.length > 0).join('\n');
}
