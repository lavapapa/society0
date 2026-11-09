import type { SqliteMigration } from '../sqlite/contracts.js';
export const actorsMigration: SqliteMigration = { owner: 'actors', from: 0, to: 1, after: [], source: { package: '@society0/core', entry: 'actors', version: 1 }, statements: [
        { sql: "CREATE TABLE s0_actors(id TEXT PRIMARY KEY NOT NULL,attributes TEXT NOT NULL,source TEXT,participation TEXT NOT NULL CHECK(participation IN ('active','inactive')),reason TEXT NOT NULL,version INTEGER NOT NULL)" }
    ] };
export const actorsMigrations = [actorsMigration] as const;
export const migrations = actorsMigrations;
