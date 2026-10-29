import { slotKey } from '@society0/host/slots';
import { posix } from 'node:path';
import type { ExplorationMount, ExplorationSession } from '../contracts/exploration.js';
import { fail } from '../information/page.js';
export interface MountRegistration {
    readonly mount: ExplorationMount;
    readonly registrationId: string;
}
export const explorationMountSlot = slotKey<MountRegistration>('society/exploration-mount');
export interface ExplorationOptions {
    maxInlineFileBytes: number;
    maxSearchBytes: number;
    maxSearchOutputBytes: number;
    maxSearchMs: number;
}
export type FindRequest = Parameters<ExplorationSession['find']>[0];
export type GrepRequest = Parameters<ExplorationSession['grep']>[0];
export function canonical(path: string) {
    if (!path.startsWith('/') || posix.normalize(path) !== path || path.includes('\0'))
        return fail('EXPLORATION_PATH');
    return path;
}
