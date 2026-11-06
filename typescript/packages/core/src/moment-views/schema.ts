import type { SqliteMigration } from '../sqlite/contracts.js';

export function momentViewsMigration(source: Readonly<Record<string, unknown>>): SqliteMigration {
    return {
        owner: 'moment-views', from: 0, to: 1, after: [], source,
        statements: [{ sql: 'CREATE TABLE s0_moment_views(moment_id TEXT PRIMARY KEY NOT NULL,revision INTEGER NOT NULL,opened_at_revision INTEGER NOT NULL)' }]
    };
}
