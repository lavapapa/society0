import { Context, Service } from '@society0/host';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
import type { ActivationIntent, ActivationAdmission } from '../contracts/runtime.js';
export const Config = Schema.object({});
export const metadata: PluginMetadata = { title: 'activation-mailbox', summary: '稳定有序信号合并', configVersion: 1, configPolicy: [], scope: 'generation', hostAbi: 'society0/host/1' };
export const api: ApiEntry = { exportPath: './activation-mailbox', provides: [{ key: 'activationPolicy', protocol: 'activationPolicy', major: 1, capabilities: [] }], requires: [] };
export function activationKey(i: ActivationIntent) { return JSON.stringify([i.actorId, i.momentId]); }
declare module '@society0/host' {
    interface Context {
        activationPolicy: ActivationPolicy;
    }
}
export class ActivationPolicy extends Service {
    static Config = Config;
    constructor(ctx: Context) { super(ctx, 'activationPolicy'); }
    admit(intent: ActivationIntent, active: readonly ActivationIntent[]): ActivationAdmission {
        const key = activationKey(intent);
        const found = active.find(i => activationKey(i) === key);
        if (found && found.driver !== intent.driver)
            return { kind: 'rejected', reason: 'ACTIVATION_IDENTITY_CONFLICT' };
        return found ? { kind: 'coalesced', into: found.id, mergedInput: [found.input, intent.input] } : { kind: 'accepted', key };
    }
}
export default ActivationPolicy;
