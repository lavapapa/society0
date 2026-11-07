import type { SqliteMigration } from '../sqlite/contracts.js';

/** 版本化公开仓库：节点、行、文件聚合与trigram候选索引。 */
export function publicMigration(source: Readonly<Record<string, unknown>>): SqliteMigration {
    return {
        owner: 'public', from: 0, to: 1, after: ['artifacts-local'], source,
        statements: [{
            sql: `CREATE TABLE public_nodes(
  path TEXT NOT NULL,parent TEXT NOT NULL,rev_from INTEGER NOT NULL,rev_to INTEGER,
  kind TEXT NOT NULL CHECK(kind IN('dir','doc','table','derived')),
  provider TEXT NOT NULL,node_id TEXT NOT NULL,
  media TEXT NOT NULL,meta TEXT NOT NULL,
  audience TEXT NOT NULL DEFAULT 'all',
  body BLOB,artifact TEXT,bytes INTEGER,
  PRIMARY KEY(path,rev_from));
CREATE INDEX public_nodes_live ON public_nodes(rev_to,path);
CREATE INDEX public_nodes_parent ON public_nodes(parent,path,rev_from);
CREATE TABLE public_rows(
  file TEXT NOT NULL,row_key TEXT NOT NULL,sort_key TEXT NOT NULL,
  rev_from INTEGER NOT NULL,rev_to INTEGER,
  audience TEXT NOT NULL DEFAULT 'all',
  line BLOB NOT NULL,
  PRIMARY KEY(file,row_key,rev_from));
CREATE INDEX public_rows_read ON public_rows(file,sort_key,row_key) WHERE rev_to IS NULL;
CREATE INDEX public_rows_hist ON public_rows(file,rev_to);
CREATE TABLE public_files(
  file TEXT NOT NULL,parent TEXT NOT NULL,rev_from INTEGER NOT NULL,rev_to INTEGER,
  table_path TEXT NOT NULL,rows INTEGER NOT NULL,bytes INTEGER NOT NULL,
  audiences TEXT NOT NULL,basename TEXT NOT NULL,extension TEXT NOT NULL,
  PRIMARY KEY(file,rev_from));
CREATE INDEX public_files_name ON public_files(extension,file,rev_from,rev_to,audiences);
CREATE INDEX public_files_parent ON public_files(parent,file,rev_from,rev_to);
CREATE INDEX public_files_live_name ON public_files(extension,file,rev_from,audiences) WHERE rev_to IS NULL;
CREATE INDEX public_files_history_name ON public_files(extension,file,rev_to,rev_from,audiences) WHERE rev_to IS NOT NULL;
CREATE INDEX public_files_changes_open ON public_files(rev_from,file,rev_to);
CREATE INDEX public_files_changes_close ON public_files(rev_to,file) WHERE rev_to IS NOT NULL;
CREATE INDEX public_nodes_doc_open ON public_nodes(rev_from,path) WHERE kind='doc';
CREATE INDEX public_nodes_doc_close ON public_nodes(rev_to,path) WHERE kind='doc' AND rev_to IS NOT NULL;
CREATE INDEX public_nodes_kind ON public_nodes(kind,path,rev_from);
CREATE VIRTUAL TABLE public_fts USING fts5(line,content='',tokenize='trigram',detail='none',columnsize=0);
CREATE VIRTUAL TABLE public_search_state USING fts5(state,content='');`
        }]
    };
}

/** v2：为旧 run 补 r1 的 as-of 查询索引；已存在的索引不重建。 */
export function publicIndexMigration(source: Readonly<Record<string, unknown>>): SqliteMigration {
    return {
        owner: 'public', from: 1, to: 2, after: [], source,
        statements: [{
            sql: `CREATE INDEX IF NOT EXISTS public_files_parent ON public_files(parent,file,rev_from,rev_to);
CREATE INDEX IF NOT EXISTS public_files_live_name ON public_files(extension,file,rev_from,audiences) WHERE rev_to IS NULL;
CREATE INDEX IF NOT EXISTS public_files_history_name ON public_files(extension,file,rev_to,rev_from,audiences) WHERE rev_to IS NOT NULL;
CREATE INDEX IF NOT EXISTS public_nodes_kind ON public_nodes(kind,path,rev_from);`
        }]
    };
}
