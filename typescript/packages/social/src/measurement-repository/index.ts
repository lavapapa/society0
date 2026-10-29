import { Context, Service } from '@society0/host';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({});
export const metadata: PluginMetadata = {
    "title": "measurement-repository", "summary": "measurement-repository", "scope": "run", "hostAbi": "society0/host/1", "configVersion": 1, "configPolicy": []
};
export const api: ApiEntry = {
    "exportPath": "./measurement-repository", "provides": [{
            "key": "measurementRepository", "protocol": "measurementRepository", "major": 1, "capabilities": []
        }], "requires": [{
            "service": {
                "key": "database", "protocol": "database", "major": 1, "capabilities": []
            }, "optional": false, "phase": "module"
        }, {
            "service": {
                "key": "artifacts", "protocol": "artifacts", "major": 1, "capabilities": []
            }, "optional": false, "phase": "module"
        }, {
            "service": {
                "key": "results", "protocol": "results", "major": 1, "capabilities": []
            }, "optional": false, "phase": "module"
        }]
};
import '@society0/core/sqlite';
import '@society0/core/artifacts-local';
import '@society0/core/results';
import type { MeasurementRecord, SqliteTransaction, SqliteReadView, PageRequest } from '../contracts.js';
import { after, page } from '../paging.js';
export * from './schema.js';
declare module '@society0/host' {
    interface Context {
        measurementRepository: MeasurementRepository;
    }
}
export class MeasurementRepository extends Service {
    static Config = Config;
    static inject = ['database', 'artifacts', 'results'];
    constructor(ctx: Context) {
        super(ctx, 'measurementRepository');
    }
    async append(tx: SqliteTransaction, record: MeasurementRecord) {
        await tx.execute({
            sql: 'INSERT INTO social_measurements(id,spec_id,kind,actor_id,logical_time,value,original,source,required) VALUES(?,?,?,?,?,?,?,?,?)', parameters: [record.id, record.specId, record.kind, record.actorId, JSON.stringify(record.logicalTime), JSON.stringify(record.value), JSON.stringify(record.original), JSON.stringify(record.source), record.required ? 1 : 0]
        });
    }
    async page(reader: SqliteReadView, request: PageRequest, filter: {
        actorId?: string;
        specId?: string;
    } = {}) {
        const clauses: string[] = [], parameters: string[] = [];
        if (filter.actorId) {
            clauses.push('actor_id=?');
            parameters.push(filter.actorId);
        }
        if (filter.specId) {
            clauses.push('spec_id=?');
            parameters.push(filter.specId);
        }
        const where = clauses.join(' AND ') || '1';
        const [head] = await reader.query({
            sql: `SELECT count(*) AS n,coalesce(max(ordinal),0) AS through FROM social_measurements WHERE ${where}`, parameters
        }, {
            maxRows: 1, maxBytes: 2048
        });
        const identity = {
            runId: reader.runId, filter, through: String(head!.through)
        };
        const last = after(request.cursor, identity) || '0';
        const rows = await reader.query({
            sql: `SELECT * FROM social_measurements WHERE ${where} AND ordinal>? ORDER BY ordinal LIMIT ?`, parameters: [...parameters, BigInt(last), request.maxItems + 1]
        }, {
            maxRows: request.maxItems + 1, maxBytes: Math.max(65536, request.maxResponseBytes * 2)
        });
        return page(rows.map(row => ({
            ordinal: String(row.ordinal), id: String(row.id), specId: String(row.spec_id), kind: String(row.kind), actorId: row.actor_id === null ? null : String(row.actor_id), logicalTime: JSON.parse(String(row.logical_time)), value: JSON.parse(String(row.value)), original: JSON.parse(String(row.original)), source: JSON.parse(String(row.source)), required: !!row.required
        })), String(head!.n), reader.revision, request, identity, x => x.ordinal);
    }
}
export default MeasurementRepository;
