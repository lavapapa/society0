import type {SqliteConnection} from '@society0/sqlite-native';
import {fault,type SqliteMigration,type SqliteSchemaVersion} from './contracts.js';
import {planMigrations} from './schema.js';
export function readSchemaVersions(db:SqliteConnection):SqliteSchemaVersion[]{return db.query('SELECT owner,version FROM s0_schema ORDER BY owner',[],{maxRows:10000,maxBytes:1048576}).map(row=>({owner:String(row.owner),version:Number(row.version)}))}
/** 原生 schema authority；公共 run worker 另行限制 revision=0，历史 worker 只传私有目标。 */
export function applySchemaMigrations(db:SqliteConnection,migrations:readonly SqliteMigration[]):SqliteSchemaVersion[]{
 if(!db.state().autocommit)throw fault('SCHEMA_ACTIVE_TRANSACTION');
 for(const migration of planMigrations(migrations,readSchemaVersions(db))){
  for(const statement of migration.statements){if(statement.parameters?.length)throw fault('SCHEMA_PARAMETERS');db.execSchema(statement.sql)}
  db.checkForeignKeys();db.begin();try{db.execute('INSERT INTO s0_schema VALUES (?,?,?) ON CONFLICT(owner) DO UPDATE SET version=excluded.version,source=excluded.source',[migration.owner,migration.to,JSON.stringify(migration.source)]);db.commit()}catch(error){db.rollback();throw error}
 }
 db.checkForeignKeys();return readSchemaVersions(db);
}
