import type { SqliteMigration } from '../sqlite/contracts.js';
export function threadMigration(source: Readonly<Record<string, unknown>>): SqliteMigration {
    return { owner: 'thread', from: 0, to: 1, after: ['artifacts-local'], source, statements: [{ sql: `CREATE TABLE threads(id TEXT PRIMARY KEY NOT NULL,actor_id TEXT NOT NULL,moment_id TEXT NOT NULL,interaction_id TEXT NOT NULL,head INTEGER NOT NULL,UNIQUE(actor_id,moment_id));
CREATE TABLE thread_messages(thread_id TEXT NOT NULL REFERENCES threads(id),seq INTEGER NOT NULL,message_id TEXT NOT NULL,role TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(thread_id,seq),UNIQUE(thread_id,message_id));
CREATE TABLE thread_consumers(thread_id TEXT NOT NULL REFERENCES threads(id),consumer_id TEXT NOT NULL,cursor TEXT NOT NULL,PRIMARY KEY(thread_id,consumer_id));
CREATE TABLE thread_contexts(thread_id TEXT NOT NULL REFERENCES threads(id),message_id TEXT NOT NULL,ordinal INTEGER NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(thread_id,message_id,ordinal));
CREATE INDEX threads_actor ON threads(actor_id,moment_id);` }] };
}
export const threadMigrations = threadMigration;
export const migrations = (source: Readonly<Record<string, unknown>>) => [threadMigration(source)];
