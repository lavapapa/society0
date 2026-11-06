import type { Context } from '@society0/host';
import type { Actions, ActionNotifier } from '../contracts/domain.js';
import type { ActivationContext, ActivationIntent, ExecutionContext } from '../contracts/runtime.js';
import type { WakeDecision, WakeKind, WakeSignal } from '../contracts/activation-policy.js';
import type { Disposer } from '../contracts/common.js';

/** 只依赖 wake 的门面，避免 notifier 与 ActivationService 形成运行时循环。 */
export interface WakeSink {
    wake(signals: readonly WakeSignal[], context: ExecutionContext): Promise<readonly {
        readonly decision: WakeDecision;
        readonly admission?: { readonly kind: 'accepted' | 'coalesced' | 'rejected' };
    }[]>;
}

/** D8 的 reason 到 C6 的 WakeKind 的对应；设计稿没有规定，这里取最直接的一一对应。 */
export function wakeKind(reason: ActivationIntent['reason']): WakeKind {
    return reason === 'request' ? 'consent' : 'dm';
}

/** 把行动回执的激活上下文适配成 wake 需要的执行上下文。 */
function execution(context: ActivationContext): ExecutionContext {
    return {
        ctx: context.ctx,
        runId: context.runId,
        generation: context.generation,
        operationId: context.operationId,
        signal: context.signal,
        database: context.database,
        lease: context.writeLease,
        logicalTime: context.logicalTime,
        selections: [],
    };
}

/** D8 ActionNotifier：notify 意图 → WakeSignal → ActivationService.wake。 */
export function actionNotifier(activation: WakeSink): ActionNotifier {
    return {
        id: 'activation/action-notify',
        async deliver(intent, context) {
            if (!context)
                return 'none';
            const signal: WakeSignal = {
                kind: wakeKind(intent.reason),
                actor: intent.actorId,
                moment: intent.momentId,
                source: intent.source,
                id: intent.id,
                input: intent.input,
                ...(intent.from !== undefined ? { from: intent.from } : {}),
            };
            const [outcome] = await activation.wake([signal], execution(context));
            if (!outcome)
                return 'none';
            if (outcome.decision === 'notify')
                return 'deferred';
            if (outcome.decision !== 'wake')
                return 'none';
            return outcome.admission?.kind === 'accepted' || outcome.admission?.kind === 'coalesced' ? 'scheduled' : 'none';
        },
    };
}

/**
 * 在 actions 服务存在时注册 notifier。activation 与 actions 的加载顺序不固定：
 * 先尝试当前实例，再监听服务注册。返回值由调用方的 effect 持有。
 */
export function registerActionNotifier(ctx: Context, activation: WakeSink): Disposer {
    const notifier = actionNotifier(activation);
    let current: { actions: Actions; dispose: Disposer } | undefined;
    const attach = (actions: Actions | undefined) => {
        if (!actions) {
            const dispose = current?.dispose;
            current = undefined;
            if (dispose)
                void dispose();
            return;
        }
        if (current)
            return;
        current = { actions, dispose: actions.provideNotifier(notifier) };
    };
    attach(ctx.get('actions'));
    const off = ctx.on('internal/service', (name, value) => {
        if (name === 'actions')
            attach(value as Actions | undefined);
    });
    return () => {
        off();
        const dispose = current?.dispose;
        current = undefined;
        return dispose?.();
    };
}
