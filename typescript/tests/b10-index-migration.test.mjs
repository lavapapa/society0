import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setup } from './b4-support.mjs';
import { artifactsMigration } from '../.build/core/dist/artifacts-local/index.js';
import { publicIndexMigration } from '../.build/core/dist/public/index.js';

const source = { consumer: 'b10-index-v2' };
// 旧 v1：只有原始表和 public_files_name，没有 r1 的四个索引。
const legacyV1 = {
    owner: 'public', from: 0, to: 1, after: ['artifacts-local'], source,
    statements: [{
        sql: `CREATE TABLE public_files(
  file TEXT NOT NULL,parent TEXT NOT NULL,rev_from INTEGER NOT NULL,rev_to INTEGER,
  table_path TEXT NOT NULL,rows INTEGER NOT NULL,bytes INTEGER NOT NULL,
  audiences TEXT NOT NULL,basename TEXT NOT NULL,extension TEXT NOT NULL,
  PRIMARY KEY(file,rev_from));
CREATE INDEX public_files_name ON public_files(extension,file,rev_from,rev_to,audiences);
CREATE TABLE public_nodes(
  path TEXT NOT NULL,parent TEXT NOT NULL,rev_from INTEGER NOT NULL,rev_to INTEGER,
  kind TEXT NOT NULL,provider TEXT NOT NULL,node_id TEXT NOT NULL,media TEXT NOT NULL,
  meta TEXT NOT NULL,audience TEXT NOT NULL DEFAULT 'all',body BLOB,artifact TEXT,bytes INTEGER,
  PRIMARY KEY(path,rev_from));`
    }]
};

// B10 返工 2：旧 v1 run 没有 r1 索引，public v2 迁移补上，且 INDEXED BY 可用。
test('B10 旧 v1 run 应用 public v2 后索引存在且 INDEXED BY 可用', async () => {
    const run = await setup({ migrate: false });
    try {
        await run.ctx.database.applyMigrations([artifactsMigration, legacyV1]);
        const before = await run.read(view => view.query({ sql: "SELECT name FROM sqlite_master WHERE type='index' AND name IN ('public_files_parent','public_files_live_name','public_files_history_name','public_nodes_kind')" }, { maxRows: 16, maxBytes: 4096 }));
        assert.deepEqual(before.map(row => String(row.name)), [], '旧 v1 不应有 r1 索引');
        await run.ctx.database.applyMigrations([publicIndexMigration(source)]);
        const after = await run.read(view => view.query({ sql: "SELECT name FROM sqlite_master WHERE type='index' AND name IN ('public_files_parent','public_files_live_name','public_files_history_name','public_nodes_kind') ORDER BY name" }, { maxRows: 16, maxBytes: 4096 }));
        assert.deepEqual(after.map(row => String(row.name)), ['public_files_history_name', 'public_files_live_name', 'public_files_parent', 'public_nodes_kind']);
        const rows = await run.read(view => view.query({ sql: 'SELECT file FROM public_files INDEXED BY public_files_live_name WHERE file>=? AND file<? AND rev_to IS NULL LIMIT 1', parameters: ['/public/', '/public0'] }, { maxRows: 1, maxBytes: 1024 }));
        assert.equal(rows.length, 0);
    }
    finally {
        await run.close();
    }
});
