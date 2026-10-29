import { fail } from '../information/page.js';
export type { Random as RandomPort } from '../contracts/random.js';
import type { Random as RandomPort } from '../contracts/random.js';
interface Address {
    run: string;
    actor: string;
    seed: string;
}
/** 计数地址沿用公共Philox；整数拒绝采样有独立draw预算。 */
class CounterIntegers {
    private index = 0;
    constructor(private random: RandomPort, private address: Address) { }
    private draw() {
        if (this.index >= 32000)
            return fail('PLAIN_SAMPLE_DRAW_BUDGET');
        return BigInt(this.random.at({ run: this.address.run, actor: this.address.actor, operation: 'plain.sample:' + this.address.seed, stream: 'plain.actors', index: String(this.index++) }).uint32);
    }
    integer(bound: bigint) {
        const width = 1n << 64n, limit = width - width % bound;
        while (true) {
            const value = (this.draw() << 32n) | this.draw();
            if (value < limit)
                return value % bound;
        }
    }
}
/** Floyd抽样仅保留所选秩，候选集合留在SQL。 */
export function sampleRanks(random: RandomPort, count: bigint, size: number, address: Address): readonly bigint[] {
    if (!Number.isSafeInteger(size) || size < 0 || size > 1000 || BigInt(size) > count)
        return fail('PLAIN_SAMPLE_BUDGET');
    const selected = new Set<bigint>(), integers = new CounterIntegers(random, address);
    for (let rank = count - BigInt(size); rank < count; rank++) {
        const chosen = integers.integer(rank + 1n);
        selected.add(selected.has(chosen) ? rank : chosen);
    }
    return [...selected].map(rank => rank + 1n);
}
