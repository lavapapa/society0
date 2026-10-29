import { Context, Service } from '@society0/host';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({ proposals: Schema.array(Schema.any()).default([]) });
export const metadata: PluginMetadata = { "title": "proposal-sequence", "summary": "proposal-sequence", "configVersion": 1, "configPolicy": [{ "path": "/proposals", "mode": "new-run", "affectsResults": true }], "scope": "generation", "hostAbi": "society0/host/1" };
export const api: ApiEntry = { "exportPath": "./proposal-sequence", "provides": [{ "key": "proposalSource", "protocol": "proposalSource", "major": 1, "capabilities": [] }], "requires": [] };
import type { Json, Proposal, ProposalResult, ProposalSource as Source, CompletePoint } from '../contracts/runtime.js';
import { integer } from '../clock-ticks/index.js';
declare module '@society0/host' {
    interface Context {
        proposalSource: Source;
    }
}
export class ProposalSource extends Service implements Source {
    static Config = Config;
    private proposals: readonly Omit<Proposal, 'cursor'>[];
    constructor(ctx: Context, config: {
        proposals?: readonly Omit<Proposal, 'cursor'>[];
    } = {}) { super(ctx, 'proposalSource'); this.proposals = structuredClone(Config({ proposals: config.proposals && [...config.proposals] }).proposals); }
    async waitProposal(cursor: Json, signal: AbortSignal): Promise<ProposalResult> {
        signal.throwIfAborted();
        const index = integer(cursor);
        if (index >= BigInt(this.proposals.length))
            return { kind: 'end', reason: 'sequence exhausted' };
        return { kind: 'proposal', proposal: { ...this.proposals[Number(index)]!, cursor } };
    }
    async acknowledge(_proposal: Proposal, _cut: CompletePoint) { }
    async release(_proposal: Proposal) { }
}
export default ProposalSource;
