import {compareCodePoints} from '@society0/host/order';
import { socialTime } from '../clock.js';
import { Context, Service } from '@society0/host';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({
    chronologicalWeight: Schema.number().min(0).default(0.3), engagementWeight: Schema.number().min(0).default(0.3), similarityWeight: Schema.number().min(0).default(0.2), networkWeight: Schema.number().min(0).default(0.2), followBonus: Schema.number().min(0).default(0.2), likeScore: Schema.number().default(1), replyScore: Schema.number().default(1.5), repostScore: Schema.number().default(2), timeDecay: Schema.number().min(1).max(Number.MAX_VALUE).default(12)
});
export const metadata: PluginMetadata = {
    "title": "social-ranking", "summary": "social-ranking", "scope": "generation", "hostAbi": "society0/host/1", "configVersion": 1, "configPolicy": [{
            "path": "/chronologicalWeight", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/engagementWeight", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/similarityWeight", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/networkWeight", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/followBonus", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/likeScore", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/replyScore", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/repostScore", "mode": "new-run", "affectsResults": true
        }, {
            "path": "/timeDecay", "mode": "new-run", "affectsResults": true
        }]
};
export const api: ApiEntry = {
    "exportPath": "./social-ranking", "provides": [{
            "key": "socialRankings", "protocol": "socialRankings", "major": 1, "capabilities": []
        }], "requires": [{
            "service": {
                "key": "slots", "protocol": "slots", "major": 1, "capabilities": []
            }, "optional": false, "phase": "module"
        }]
};
import { registerNamed, acquireNamed } from '../registry.js';
import type { SocialRanking, RecommendationInput, RankedItem } from '../contracts.js';
declare module '@society0/host' {
    interface Context {
        socialRankings: SocialRankings;
    }
}
type Weights = ReturnType<typeof Config>;
type Item = RecommendationInput['items'][number];
function time(item: Item) {
    return BigInt((item.features as any).createdTime ?? '0');
}
function chronology(a: Item, b: Item) {
    if (time(a) > time(b))
        return -1;
    if (time(a) < time(b))
        return 1;
    return compareCodePoints(a.id, b.id);
}
function chronological(input: RecommendationInput) {
    const sorted = [...input.items].sort(chronology);
    return sorted.map((item, i) => ({
        id: item.id, score: sorted.length - i, components: {
            createdTime: String(time(item)), position: i, totalScore: sorted.length - i
        }
    }));
}
function recency(item: Item, now: bigint, decay: number) {
    const delta = now > time(item) ? now - time(item) : 0n;
    const unit = BigInt(Math.floor(decay));
    const age = Number(delta / unit) * (Number(unit) / decay) + Number(delta % unit) / decay;
    return Math.exp(-age);
}
function engagement(feature: any, config: Weights) {
    return Number(feature.likes ?? 0) * config.likeScore + Number(feature.replies ?? 0) * config.replyScore + Number(feature.reposts ?? 0) * config.repostScore;
}
function weightedItem(item: Item, now: bigint, config: Weights): RankedItem {
    const feature = item.features as any, timeScore = recency(item, now, config.timeDecay), engagementScore = engagement(feature, config), networkScore = feature.following ? config.followBonus : 0, semanticScore = Number(feature.similarity ?? 0);
    const components = {
        timeScore, timeContribution: config.chronologicalWeight * timeScore, engagementScore, engagementContribution: config.engagementWeight * engagementScore, networkScore, networkContribution: config.networkWeight * networkScore, semanticScore, semanticContribution: config.similarityWeight * semanticScore
    };
    const score = components.timeContribution + components.engagementContribution + components.networkContribution + components.semanticContribution;
    return {
        id: item.id, score, components: {
            ...components, totalScore: score
        }
    };
}
function rankingTime(input: RecommendationInput) {
    const query = input.scope.query;
    if (query === null || typeof query !== 'object' || !('time' in query))
        throw Error('SOCIAL_RECOMMENDATION_TIME_REQUIRED');
    return BigInt(socialTime(query.time));
}
function weighted(input: RecommendationInput, config: Weights) {
    const now = rankingTime(input), byId = new Map(input.items.map(item => [item.id, item]));
    return input.items.map(item => weightedItem(item, now, config)).sort((a, b) => b.score - a.score || chronology(byId.get(a.id)!, byId.get(b.id)!));
}
export class SocialRankings extends Service {
    static Config = Config;
    static inject = ['slots'];
    constructor(ctx: Context, input = {}) {
        super(ctx, 'socialRankings');
        const config = Config(input), source = {
            package: '@society0/social', entry: 'social-ranking', version: '0.1.0'
        };
        this.register({
            id: 'chronological/v1', source, configId: 'chronological/v1', rank: chronological
        });
        this.register({
            id: 'weighted/v1', source, configId: JSON.stringify(config), rank: input => weighted(input, config)
        });
    }
    register(strategy: SocialRanking) {
        return registerNamed(this.ctx, 'society/social-ranking', strategy.id, strategy, value => {
            if (typeof (value as SocialRanking)?.rank !== 'function')
                throw Error('SOCIAL_RANKING');
            return value as SocialRanking;
        });
    }
    acquire(id: string) {
        return acquireNamed<SocialRanking>(this.ctx, 'society/social-ranking', id);
    }
}
export function validateRanking(input: RecommendationInput, ranked: readonly RankedItem[]) {
    const ids = new Set(input.items.map(x => x.id));
    if (ids.size !== input.items.length || ranked.length !== ids.size)
        throw Error('SOCIAL_RANKING_COVERAGE');
    const seen = new Set<string>();
    for (const item of ranked) {
        if (!ids.has(item.id) || seen.has(item.id) || !Number.isFinite(item.score))
            throw Error('SOCIAL_RANKING_OUTPUT');
        seen.add(item.id);
    }
}
export default SocialRankings;
