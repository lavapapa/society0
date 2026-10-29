import type { SqliteMigration } from '@society0/core/sqlite/contracts';
export const socialMigration: SqliteMigration = {
    owner: 'social', from: 0, to: 1, after: ['actors'], source: {
        package: '@society0/social', entry: 'social-repository', version: 1
    }, statements: [{
            sql: `
CREATE TABLE social_members(id TEXT PRIMARY KEY NOT NULL REFERENCES s0_actors(id),ordinal INTEGER NOT NULL UNIQUE,preferences TEXT NOT NULL);
CREATE TABLE social_versions(owner TEXT PRIMARY KEY NOT NULL,version INTEGER NOT NULL);
CREATE TABLE social_edges(ordinal INTEGER PRIMARY KEY,follower TEXT NOT NULL REFERENCES social_members(id),followee TEXT NOT NULL REFERENCES social_members(id),created_time TEXT NOT NULL,UNIQUE(follower,followee),CHECK(follower<>followee));
CREATE INDEX social_edges_in ON social_edges(followee,follower);CREATE INDEX social_edges_out ON social_edges(follower,followee);
CREATE TABLE social_posts(ordinal INTEGER PRIMARY KEY,id TEXT NOT NULL UNIQUE,author_id TEXT NOT NULL REFERENCES social_members(id),content TEXT NOT NULL,tags TEXT NOT NULL,created_time TEXT NOT NULL,kind TEXT NOT NULL CHECK(kind IN ('post','reply','repost')),parent_id TEXT REFERENCES social_posts(id),text_version INTEGER NOT NULL,likes INTEGER NOT NULL,replies INTEGER NOT NULL,reposts INTEGER NOT NULL,views INTEGER NOT NULL,metadata TEXT NOT NULL);
CREATE INDEX social_posts_author ON social_posts(author_id,ordinal);CREATE INDEX social_posts_parent ON social_posts(parent_id,ordinal);
CREATE TABLE social_comments(id TEXT PRIMARY KEY NOT NULL REFERENCES social_posts(id),post TEXT NOT NULL REFERENCES social_posts(id),author TEXT NOT NULL REFERENCES social_members(id),content TEXT NOT NULL,created_time TEXT NOT NULL);
CREATE TABLE social_reactions(ordinal INTEGER PRIMARY KEY,post TEXT NOT NULL REFERENCES social_posts(id),actor TEXT NOT NULL REFERENCES social_members(id),kind TEXT NOT NULL,created_time TEXT NOT NULL,UNIQUE(post,actor,kind));
CREATE TABLE social_deliveries(ordinal INTEGER PRIMARY KEY,event_id TEXT NOT NULL,post TEXT NOT NULL REFERENCES social_posts(id),recipient TEXT NOT NULL REFERENCES social_members(id),sender TEXT NOT NULL REFERENCES social_members(id),created_time TEXT NOT NULL,UNIQUE(event_id,recipient));CREATE INDEX social_inbox ON social_deliveries(recipient,ordinal);
CREATE TABLE social_notifications(ordinal INTEGER PRIMARY KEY,id TEXT NOT NULL UNIQUE,actor TEXT NOT NULL REFERENCES social_members(id),kind TEXT NOT NULL,created_time TEXT NOT NULL,consumed INTEGER NOT NULL,data TEXT NOT NULL);CREATE INDEX social_unread ON social_notifications(actor,consumed,ordinal);
CREATE TABLE social_events(ordinal INTEGER PRIMARY KEY,id TEXT NOT NULL UNIQUE,actor TEXT NOT NULL REFERENCES social_members(id),kind TEXT NOT NULL,target TEXT NOT NULL,created_time TEXT NOT NULL,data TEXT NOT NULL);
CREATE TABLE social_presentations(id TEXT PRIMARY KEY NOT NULL,actor TEXT NOT NULL REFERENCES social_members(id),moment TEXT NOT NULL,input_id TEXT NOT NULL,request TEXT NOT NULL);
CREATE TABLE social_exposures(presentation_id TEXT NOT NULL REFERENCES social_presentations(id),post TEXT NOT NULL REFERENCES social_posts(id),actor TEXT NOT NULL REFERENCES social_members(id),PRIMARY KEY(presentation_id,post));
CREATE TABLE social_recommended(actor TEXT PRIMARY KEY NOT NULL REFERENCES social_members(id),input_id TEXT NOT NULL,ids TEXT NOT NULL);
CREATE TABLE social_tags(post TEXT NOT NULL REFERENCES social_posts(id),tag TEXT NOT NULL,PRIMARY KEY(post,tag));
`
        }]
};
export const socialMigrations = [socialMigration] as const;
