// Opening a connection must honour timeoutMs for the open-time pragmas. Sibling branches open the
// shared lineage attempts ledger from separate processes; on a slow shared filesystem a lock held
// by a sibling at open time made `PRAGMA journal_mode=WAL` fail with "database is locked" because
// busy_timeout was applied only after it.
import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { openConnection } from '../.build/sqlite-native/src/index.mjs';

const holder = `
import {DatabaseSync} from 'node:sqlite';
const db = new DatabaseSync(process.argv[1]);
db.exec('PRAGMA journal_mode=DELETE; CREATE TABLE IF NOT EXISTS t(v); BEGIN EXCLUSIVE; INSERT INTO t VALUES(1)');
process.stdout.write('held\\n');
Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, Number(process.argv[2]));
db.exec('COMMIT'); db.close();
`;

test('openConnection waits for a lock held by another process instead of failing at open', async () => {
    const path = join(mkdtempSync(join(tmpdir(), 'sqlite-native-open-busy-')), 'shared.sqlite');
    const child = spawn(process.execPath, ['--no-warnings', '--input-type=module', '-e', holder, path, '500'], { stdio: ['ignore', 'pipe', 'inherit'] });
    const exited = once(child, 'exit');
    for await (const chunk of child.stdout) if (String(chunk).includes('held')) break;
    const started = Date.now();
    const db = openConnection({ path, timeoutMs: 5000 });
    try {
        assert.ok(Date.now() - started >= 200, 'open should have waited for the holder');
        assert.equal(db.query('SELECT count(*) AS n FROM t', [], { maxRows: 1, maxBytes: 256 })[0].n, 1n);
    } finally {
        db.close();
        await exited;
    }
});
