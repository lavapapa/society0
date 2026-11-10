import type { SqliteMigration } from '@society0/core/sqlite/contracts';
export const semanticMigration: SqliteMigration = {
    owner: 'social-semantic', from: 0, to: 1, after: ['social'], source: {
        package: '@society0/social', entry: 'social-semantic', version: 1
    }, statements: [{
            sql: `CREATE TABLE social_semantic_pending(post_id TEXT PRIMARY KEY NOT NULL REFERENCES social_posts(id),text_version INTEGER NOT NULL,content TEXT NOT NULL);
CREATE TABLE social_semantic_vectors(post_id TEXT PRIMARY KEY NOT NULL REFERENCES social_posts(id),text_version INTEGER NOT NULL,identity TEXT NOT NULL,vector TEXT NOT NULL,origin TEXT NOT NULL,revision INTEGER NOT NULL);
CREATE TABLE social_semantic_changes(revision INTEGER NOT NULL,id TEXT NOT NULL,kind TEXT NOT NULL,actor_id TEXT NOT NULL,vector TEXT,PRIMARY KEY(revision,id));
CREATE TABLE social_semantic_progress(run_id TEXT NOT NULL,generation TEXT NOT NULL,space TEXT NOT NULL,identity TEXT NOT NULL,distance TEXT NOT NULL,progress TEXT NOT NULL,PRIMARY KEY(run_id,generation,space,identity,distance));`
        }]
};
export const semanticMigrations = [semanticMigration] as const;
export const migrations = semanticMigrations;
