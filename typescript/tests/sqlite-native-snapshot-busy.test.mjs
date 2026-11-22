// Readonly connections back the pooled snapshot readers of a running world. Under load on a shared
// filesystem a reader can meet a lock; it must wait up to timeoutMs like a writer does, and when it
// still fails, beginSnapshot must leave the connection without an open transaction. Otherwise the
// pool reuses the connection and every later snapshot fails with
// "cannot start a transaction within a transaction", which killed a formal run mid-tick.
import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { openConnection } from '../.build/sqlite-native/src/index.mjs';

// An exclusive rollback-journal transaction: while this process holds it, other readers get SQLITE_BUSY.
const holder = `
import {DatabaseSync} from 'node:sqlite';
const db = new DatabaseSync(process.argv[1]);
db.exec('BEGIN EXCLUSIVE; INSERT INTO t VALUES(2)');
process.stdout.write('held\\n');
Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, Number(process.argv[2]));
db.exec('COMMIT'); db.close();
`;

function world() {
    const path = join(mkdtempSync(join(tmpdir(), 'sqlite-native-snapshot-busy-')), 'world.sqlite');
    const db = new DatabaseSync(path);
    db.exec('CREATE TABLE t(v); INSERT INTO t VALUES(1)');
    db.close();
    return path;
}

async function hold(path, ms) {
    const child = spawn(process.execPath, ['--no-warnings', '--input-type=module', '-e', holder, path, String(ms)], { stdio: ['ignore', 'pipe', 'inherit'] });
    const exited = once(child, 'exit');
    for await (const chunk of child.stdout) if (String(chunk).includes('held')) break;
    return { exited };
}

const count = db => db.query('SELECT count(*) AS n FROM t', [], { maxRows: 1, maxBytes: 256 })[0].n;

test('a readonly snapshot waits for a lock held by another process', async () => {
    const path = world();
    const db = openConnection({ path, readOnly: true, timeoutMs: 5000 });
    const { exited } = await hold(path, 500);
    try {
        const started = Date.now();
        db.beginSnapshot({ maxAgeMs: 10000 });
        assert.ok(Date.now() - started >= 200, 'snapshot should have waited for the holder');
        assert.equal(count(db), 2n);
        db.endSnapshot();
    } finally {
        db.close();
        await exited;
    }
});

test('a failed beginSnapshot leaves the connection reusable', async () => {
    const path = world();
    const db = openConnection({ path, readOnly: true, timeoutMs: 100 });
    const { exited } = await hold(path, 1000);
    try {
        assert.throws(() => db.beginSnapshot({ maxAgeMs: 10000 }), error => error.code === 'SQLITE_BUSY');
        assert.equal(db.state().autocommit, true, 'no transaction may stay open after a failed snapshot');
        await exited;
        db.beginSnapshot({ maxAgeMs: 10000 });
        assert.equal(count(db), 2n);
        db.endSnapshot();
    } finally {
        db.close();
    }
});
