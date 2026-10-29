import type { ActivationService } from '../activation/index.js';
import type { ActivationResult, AdvanceResult, ExecutionContext } from '../contracts/runtime.js';
export function activationStatus(rejected: boolean, results: readonly ActivationResult[]): AdvanceResult['status'] {
    if (rejected || results.some(result => result.status === 'incomplete')) return 'incomplete';
    return results.some(result => result.status === 'waiting') ? 'waiting' : 'settled';
}
/** 两类计划共享取消、物理等待和批次归还；计划解释仍属于各Runtime。 */
export class RuntimeWork {
    private active = new Set<Promise<unknown>>();
    private contexts = new Set<ExecutionContext>();
    constructor(private activation: Pick<ActivationService,'release'|'cancel'>) {}
    track<T>(context: ExecutionContext, task: Promise<T>): Promise<T> {
        this.active.add(task);
        this.contexts.add(context);
        void task.finally(() => {
            this.active.delete(task);
            this.contexts.delete(context);
            this.activation.release(context);
        }).catch(() => {});
        return task;
    }
    async drain(reason: string) {
        await Promise.allSettled([...this.contexts].map(context => this.activation.cancel(context,reason)));
        await Promise.allSettled([...this.active]);
    }
}
