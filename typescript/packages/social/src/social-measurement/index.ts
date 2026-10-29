import { Context, Service } from '@society0/host';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({
    maxRecordBytes: Schema.natural().min(1).default(16777216)
});
export const metadata: PluginMetadata = {
    "title": "social-measurement", "summary": "social-measurement", "scope": "generation", "hostAbi": "society0/host/1", "configVersion": 1, "configPolicy": [{
            "path": "/maxRecordBytes", "mode": "new-run", "affectsResults": true
        }]
};
export const api: ApiEntry = {
    "exportPath": "./social-measurement", "provides": [{
            "key": "recommendationMeasurement", "protocol": "recommendationMeasurement", "major": 1, "capabilities": []
        }], "requires": [{
            "service": {
                "key": "recommendations", "protocol": "recommendations", "major": 1, "capabilities": []
            }, "optional": false, "phase": "module"
        }, {
            "service": {
                "key": "socialRankings", "protocol": "socialRankings", "major": 1, "capabilities": []
            }, "optional": false, "phase": "module"
        }, {
            "service": {
                "key": "results", "protocol": "results", "major": 1, "capabilities": []
            }, "optional": false, "phase": "module"
        }]
};
import { randomUUID } from 'node:crypto';
import '@society0/core/results';
import '../social-recommendations/index.js';
import { validateRanking } from '../social-ranking/index.js';
import type { WriteLease } from '@society0/core/execution-fence';
import type { RecommendationInput, Invocation, Json } from '../contracts.js';
declare module '@society0/host' {
    interface Context {
        recommendationMeasurement: RecommendationMeasurement;
    }
}
export class RecommendationMeasurement extends Service {
    static Config = Config;
    static inject = ['recommendations', 'socialRankings', 'results'];
    private config: ReturnType<typeof Config>;
    constructor(ctx: Context, input = {}) {
        super(ctx, 'recommendationMeasurement');
        this.config = Config(input);
    }
    async compare(input: RecommendationInput, strategies: readonly string[], lease: WriteLease, context: Invocation) {
        context.signal.throwIfAborted();
        this.ctx.recommendations.assertInput(input);
        if (input.runId !== lease.runId || context.runId !== lease.runId || lease.generation !== context.generation)
            throw Error('MEASUREMENT_RUN');
        const selected = [];
        try {
            for (const strategy of strategies)
                selected.push(await this.ctx.socialRankings.acquire(strategy));
            const rows: Json[] = [];
            for (const held of selected) {
                const ranked = held.value.rank(structuredClone(input));
                validateRanking(input, ranked);
                rows.push({
                    input: input as unknown as Json, strategy: held.value.id, source: held.value.source, configId: held.value.configId, ranked: ranked as unknown as Json
                });
            }
            const resultSetId = 'ranking-' + randomUUID();
            await this.ctx.results.write(lease, context.operationId, resultSetId, (async function* () {
                yield* rows;
            })(), {
                maxBatchRows: 16, maxInputBytes: 1048576, maxCapturedBytes: 4194304, maxRecordBytes: this.config.maxRecordBytes
            }, context.signal);
            return {
                resultSetId, requiredOperations: [context.operationId + ':finish']
            };
        }
        finally {
            for (const held of selected)
                await held.release();
        }
    }
}
export default RecommendationMeasurement;
