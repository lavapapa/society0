import { test } from 'node:test';
import assert from 'node:assert/strict';
import { shellEnv } from './b6-support.mjs';

test('B6 glob 展开：cat /workspace/data/*.jsonl | wc -l', async () => {
    const f = await shellEnv();
    try {
        const created = await f.ctx.explorationShell.execute(
            "mkdir -p /workspace/data; printf '1\\n2\\n' >/workspace/data/a.jsonl; printf '3\\n4\\n5\\n' >/workspace/data/b.jsonl",
            f.session.value, f.context);
        assert.equal(created.exitCode, 0, created.stderr);
        const result = await f.ctx.explorationShell.execute('cat /workspace/data/*.jsonl | wc -l', f.session.value, f.context);
        assert.equal(result.exitCode, 0, result.stderr);
        assert.equal(result.stdout.trim(), '5');
    }
    finally { await f.close(); }
});
