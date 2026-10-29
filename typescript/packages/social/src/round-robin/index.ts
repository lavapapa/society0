import { Context, Service } from '@society0/host';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({
    actors: Schema.array(Schema.string()).required(), rounds: Schema.natural().min(1).default(1), driver: Schema.string().default('rule')
});
export const metadata: PluginMetadata = {
    "title": "round-robin", "summary": "round-robin", "scope": "generation", "hostAbi": "society0/host/1", "configVersion": 1, "configPolicy": [{
            "path": "/actors", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/rounds", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/driver", "mode": "new-run", "affectsResults": true
        }]
};
export const api: ApiEntry = {
    "exportPath": "./round-robin", "provides": [{
            "key": "proposalSource", "protocol": "proposalSource", "major": 1, "capabilities": []
        }], "requires": []
};
import type { Json, Proposal, ProposalResult, CompletePoint, ProposalSource as Source } from '@society0/core/contracts/runtime';
import '@society0/core/proposal-sequence';
export class RoundRobin extends Service implements Source {
    static Config = Config;
    private config: ReturnType<typeof Config>;
    constructor(ctx: Context, input: {
        actors: string[];
        rounds?: number;
        driver?: string;
    }) {
        super(ctx, 'proposalSource');
        this.config = Config(input);
        if (!this.config.actors.length || new Set(this.config.actors).size !== this.config.actors.length)
            throw Error('ROUND_ROBIN_ACTORS');
    }
    async waitProposal(cursor: Json, signal: AbortSignal): Promise<ProposalResult> {
        signal.throwIfAborted();
        if (typeof cursor !== 'string' || !/^(0|[1-9][0-9]*)$/.test(cursor))
            throw Error('ROUND_ROBIN_CURSOR');
        const position = BigInt(cursor), size = BigInt(this.config.actors.length);
        if (position >= size * BigInt(this.config.rounds))
            return {
                kind: 'end', reason: 'round-robin exhausted'
            };
        const actorId = this.config.actors[Number(position % size)]!;
        const round = String(position / size);
        return {
            kind: 'proposal', proposal: {
                id: 'round-robin/' + cursor, cursor, planKind: 'staged/1', payload: {
                    phases: [{
                            id: 'turn', activations: [{
                                    id: 'turn/' + cursor, actorId, momentId: 'round/' + round, source: 'round-robin', driver: this.config.driver, input: {
                                        round, ordinal: cursor
                                    }
                                }]
                        }], nextCursor: String(position + 1n)
                }
            }
        };
    }
    async acknowledge(_proposal: Proposal, _cut: CompletePoint) {
    }
    async release(_proposal: Proposal) {
    }
}
export default RoundRobin;
