import type { SqliteMigration } from '@society0/core/sqlite/contracts';
export const measurementMigration: SqliteMigration = {
    owner: 'social-measurements', from: 0, to: 1, after: ['artifacts-local', 'results'], source: {
        package: '@society0/social', entry: 'measurement-repository', version: 1
    }, statements: [{
            sql: 'CREATE TABLE social_measurements(ordinal INTEGER PRIMARY KEY,id TEXT NOT NULL UNIQUE,spec_id TEXT NOT NULL,kind TEXT NOT NULL,actor_id TEXT,logical_time TEXT NOT NULL,value TEXT NOT NULL,original TEXT NOT NULL,source TEXT NOT NULL,required INTEGER NOT NULL);CREATE INDEX social_measurement_actor ON social_measurements(actor_id,ordinal);CREATE INDEX social_measurement_spec ON social_measurements(spec_id,ordinal)'
        }]
};
export const measurementMigrations = [measurementMigration] as const;
