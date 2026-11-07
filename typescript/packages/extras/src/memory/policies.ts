import { slotKey } from '@society0/host/slots';
import type { MemoryWritePolicy } from '@society0/core/contracts/cognition';
import type { Page, PageRequest } from '@society0/core/contracts/data';
import type { Json } from '@society0/core/contracts/common';
import type { SqliteReadView } from '@society0/core/sqlite';
import type { Experience } from '../memory-repository/index.js';
export type { MemoryWritePolicy };
export interface MemoryQuery extends PageRequest {
    readonly actorId: string;
    readonly text?: string;
    /** 消费方等待 deferred 产出的逻辑 tick；省略时保持旧的同步读取。 */
    readonly logicalTime?: Json;
}
export interface MemoryRecallPolicy {
    recall(query: MemoryQuery, view: SqliteReadView): Promise<Page<Experience>>;
}
export const memoryWrite = slotKey<MemoryWritePolicy>('society/memory-write');
export const memoryRecall = slotKey<MemoryRecallPolicy>('society/memory-recall');
import { fail } from '@society0/core/information/page';
function isWrite(value: unknown): value is MemoryWritePolicy { return !!value && typeof value === 'object' && 'extract' in value && typeof value.extract === 'function'; }
function isRecall(value: unknown): value is MemoryRecallPolicy { return !!value && typeof value === 'object' && 'recall' in value && typeof value.recall === 'function'; }
export function validateWrite(value: unknown): MemoryWritePolicy { if (!isWrite(value))
    fail('MEMORY_WRITE_POLICY'); return value; }
export function validateRecall(value: unknown): MemoryRecallPolicy { if (!isRecall(value))
    fail('MEMORY_RECALL_POLICY'); return value; }
