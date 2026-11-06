import type { Json } from './common.js';

/** 唤醒信号的种类；`string` 保留给未识别的通道，默认按 notify 处理。 */
export type WakeKind = 'broadcast' | 'dm' | 'consent' | 'receipt' | 'clock' | 'agenda' | 'public' | string;

/** 一次唤醒请求。moment 是时间轴锚点，也是策略里的 tick 键。 */
export interface WakeSignal {
    readonly kind: WakeKind;
    readonly actor: string;
    readonly moment: string;
    readonly source: string;
    readonly id?: string;
    readonly bytes?: number;
    /** 发起方；有值时参与每对主体的轮次上限。 */
    readonly from?: string;
    /** 唤醒后使用的 driver；缺省由调用方决定。 */
    readonly driver?: string;
    readonly input?: Json;
    readonly requiresResponse?: boolean;
}

export type WakeDecision = 'wake' | 'notify' | 'drop';

/** 策略在本 tick 内可读的计数。服务在决定为 wake 后记录。 */
export interface WakeLedger {
    wakeCount(actor: string, moment: string): number;
    pairTurns(from: string, actor: string, moment: string): number;
}

export interface ActivationPolicy {
    decide(signal: WakeSignal, ledger: WakeLedger): WakeDecision;
}
