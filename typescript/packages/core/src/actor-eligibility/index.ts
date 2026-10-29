import { Retention } from '@society0/host/composition-strategies';
import { Context, Service } from '@society0/host';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({});
export const metadata: PluginMetadata = {
    "title": "actor-eligibility", "summary": "actor-eligibility", "configVersion": 1, "configPolicy": [], "scope": "generation", "hostAbi": "society0/host/1"
};
export const api: ApiEntry = {
    "exportPath": "./actor-eligibility", "provides": [{
            "key": "actorEligibility", "protocol": "actorEligibility", "major": 1, "capabilities": []
        }], "requires": [{
            "service": {
                "key": "actors", "protocol": "actors", "major": 1, "capabilities": []
            }, "optional": false, "phase": "module"
        }, {
            "service": {
                "key": "database", "protocol": "database", "major": 1, "capabilities": []
            }, "optional": false, "phase": "module"
        }]
};
import '../actors/index.js';
import type { EligibilityRule, EligibilityRequest, EligibilityDecision, SqliteReader } from '../contracts/eligibility.js';
export type * from '../contracts/eligibility.js';
declare module '@society0/host' {
    interface Context {
        actorEligibility: ActorEligibility;
    }
}
export class ActorEligibility extends Service {
    static Config = Config;
    static inject = ['actors', 'database'];
    private rules = new Map<string, {
        rule: EligibilityRule;
        retention: Retention;
    }>();
    constructor(ctx: Context) {
        super(ctx, 'actorEligibility');
    }
    register(rule: EligibilityRule) {
        if (this.rules.has(rule.id))
            throw Error('ELIGIBILITY_DUPLICATE ' + rule.id);
        const record = {
            rule, retention: new Retention()
        };
        this.rules.set(rule.id, record);
        return this.ctx.effect(() => async () => {
            this.rules.delete(rule.id);
            await record.retention.retire();
        });
    }
    private async actorFinding(request: EligibilityRequest, reader: SqliteReader): Promise<EligibilityDecision['findings'][number]> {
        const actor = await this.ctx.actors.state(request.actorId, reader);
        if (!actor)
            return {
                ruleId: 'actors', finding: {
                    allowed: false, reason: 'missing', facts: {
                        actorId: request.actorId, participation: null, reason: null
                    }, versions: []
                }
            };
        const active = actor.participation === 'active';
        return {
            ruleId: 'actors', finding: {
                allowed: active, reason: active ? 'active' : 'inactive', facts: {
                    actorId: request.actorId, participation: actor.participation, reason: actor.reason ?? null
                }, versions: [{
                        owner: 'actors/' + request.actorId, version: actor.version
                    }]
            }
        };
    }
    async evaluate(request: EligibilityRequest, reader: SqliteReader): Promise<EligibilityDecision> {
        const records = [...this.rules.values()];
        const releases = records.map(record => record.retention.hold());
        try {
            const findings: EligibilityDecision['findings'][number][] = [await this.actorFinding(request, reader)];
            for (const r of records)
                findings.push({
                    ruleId: r.rule.id, finding: await r.rule.evaluate(request, reader)
                });
            return {
                allowed: findings.every(x => x.finding.allowed), findings
            };
        }
        finally {
            await Promise.all(releases.map(release => release()));
        }
    }
}
export default ActorEligibility;
