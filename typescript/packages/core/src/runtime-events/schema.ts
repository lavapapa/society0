import type { SqliteMigration } from '../sqlite/contracts.js';
export const eventsMigration: SqliteMigration = { owner: 'runtime-events', from: 0, to: 1, after: [], source: { package: '@society0/core', version: 1 }, statements: [{ sql: 'CREATE TABLE s0_runtime_events(source TEXT NOT NULL,id TEXT NOT NULL,offset TEXT NOT NULL,PRIMARY KEY(source,id),UNIQUE(source,offset))' }] };
