import { slotKey } from '@society0/host/slots';
import type { PerceptionSource } from '../contracts/perception.js';
export interface SourceRegistration {
    readonly source: PerceptionSource;
    readonly registrationId: string;
}
export const perceptionSourceSlot = slotKey<SourceRegistration>('society/perception-source');
