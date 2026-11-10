import type { Json } from '../contracts/common.js';
import type { ActionOffer, ActionRef, ActionTargets } from '../contracts/affordance.js';
import { targetText } from '../contracts/affordance.js';

/** 拒绝回执里可整串照抄的行动调用；target 始终是完整的 kind:id。 */
export interface SuggestedCall {
    readonly act: string;
    readonly target?: string;
    readonly prefill?: Json;
    readonly example?: Json;
    /** 该行动不接受 target 等说明；与 target 互斥。 */
    readonly note?: string;
}

/** 建议数量有界，避免把拒绝回执撑大。 */
export const SUGGESTED_CALL_LIMIT = 3;

function exampleField(example: Json | undefined): { example?: Json } {
    return example === undefined ? {} : { example };
}

function callOf(offer: ActionOffer, example: Json | undefined): SuggestedCall {
    return {
        act: offer.action,
        ...(offer.target ? { target: targetText(offer.target) } : {}),
        ...(offer.prefill !== undefined ? { prefill: offer.prefill } : {}),
        ...exampleField(example),
    };
}

/**
 * 从当前 offer 集生成最多 3 条建议。同一行动的 target 优先；该行动当前没有
 * offer 时退回当前可选的行动名，不凭历史或猜测补对象。
 */
export function suggestedCallsFromOffers(offers: readonly ActionOffer[], action: string, options: {
    readonly targets?: ActionTargets;
    readonly issue?: 'unexpected' | 'missing' | 'kind' | 'absent';
    readonly example?: Json;
} = {}): SuggestedCall[] {
    if (options.issue === 'unexpected')
        return [{ act: action, ...exampleField(options.example), note: '此行动不需要 target' }];
    const same = offers.filter((offer) => offer.action === action);
    const source = same.length ? same : offers;
    const values: SuggestedCall[] = [];
    const seen = new Set<string>();
    for (const offer of source) {
        const key = offer.target ? targetText(offer.target) : '-';
        if (seen.has(key))
            continue;
        seen.add(key);
        values.push(callOf(offer, options.example));
        if (values.length >= SUGGESTED_CALL_LIMIT)
            break;
    }
    return values;
}

/** 从执行期 alternatives 生成建议；顺序与 alternatives 一致，并去重。 */
export function suggestedCallsFromRefs(alternatives: readonly ActionRef[], example: Json | undefined): SuggestedCall[] {
    const values: SuggestedCall[] = [];
    const seen = new Set<string>();
    for (const alternative of alternatives) {
        const key = `${alternative.act}\u0000${alternative.target ?? ''}`;
        if (seen.has(key))
            continue;
        seen.add(key);
        values.push({
            act: alternative.act,
            ...(alternative.target !== undefined ? { target: alternative.target } : {}),
            ...(alternative.prefill !== undefined ? { prefill: alternative.prefill } : {}),
            ...exampleField(example),
        });
        if (values.length >= SUGGESTED_CALL_LIMIT)
            break;
    }
    return values;
}
