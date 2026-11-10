import type {Json} from '../contracts/common.js';

/** 领域规则拒绝：带 rejection 标记的错误由执行器转成 rejected 回执，不计 attempt 失败。 */
export interface ActionRejectionError extends Error {
    readonly rejection: true;
    readonly code: string;
    readonly details?: Json;
}

/**
 * 声明“这是规则拒绝，不是执行故障”。
 * 只应由 ActionDefinition.execute 抛出；执行器会保留 code/message/details 并返回 rejected 回执。
 */
export function actionRejection(code: string, message: string, details?: Json): ActionRejectionError {
    return Object.assign(new Error(message), {
        name: 'ActionRejection',
        rejection: true as const,
        code,
        ...(details === undefined ? {} : { details }),
    });
}

/** 判断错误是否由 actionRejection 产生；只认显式标记，不按错误码猜测。 */
export function isActionRejection(value: unknown): value is ActionRejectionError {
    if (value === null || typeof value !== 'object')
        return false;
    const item = value as { rejection?: unknown; code?: unknown };
    return item.rejection === true && typeof item.code === 'string' && item.code.length > 0;
}
