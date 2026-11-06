import type {ActionReceipt} from '../contracts/domain.js';
/** 业务拒绝不改变世界，也不应把整次激活判为失败；只有这些代码算可恢复拒绝。 */
export const recoverableActionCodes:ReadonlySet<string>=new Set(['ACTION_EXPIRED','ACTION_NOT_OFFERED','ACTION_INELIGIBLE','ACTION_INPUT','ACTION_INPUT_LOCKED','ACTION_TARGET_INVALID']);
export function recoverableRejection(receipt:ActionReceipt){if(receipt.status!=='rejected')return false;const code=(receipt.value as {code?:unknown}|null)?.code;return typeof code==='string'&&recoverableActionCodes.has(code)}
