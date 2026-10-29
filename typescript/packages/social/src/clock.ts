/** Social领域使用非负整数时标；事件时钟投影其time字段。 */
export function socialTime(time: unknown): string {
    const scalar = time !== null && typeof time === 'object' && 'time' in time ? time.time : time;
    if (typeof scalar === 'number' && !Number.isSafeInteger(scalar))
        throw Error('SOCIAL_TIME requires a safe integer or exact decimal string');
    const value = String(scalar);
    if (!/^(0|[1-9][0-9]*)$/.test(value))
        throw Error('SOCIAL_TIME requires non-negative integer ticks or event.time');
    return value;
}
