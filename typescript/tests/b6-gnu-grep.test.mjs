import { test } from 'node:test';
import assert from 'node:assert/strict';
import { shellEnv } from './b6-support.mjs';

test('B6 GNU grep 输出 path:line:text 并支持 -i/-l', async () => {
    const f = await shellEnv();
    try {
        const setupCommand = "mkdir -p /workspace/docs; printf 'alpha\\nfoo one\\nbeta\\n' >/workspace/docs/a.txt; printf 'prefix\\nFOO two\\n' >/workspace/docs/b.txt";
        const created = await f.ctx.explorationShell.execute(setupCommand, f.session.value, f.context);
        assert.equal(created.exitCode, 0, created.stderr);

        const result = await f.ctx.explorationShell.execute('grep -rn foo /workspace/docs | head -3', f.session.value, f.context);
        assert.equal(result.exitCode, 0, result.stderr);
        assert.deepEqual(result.stdout.trim().split('\n').sort(), [
            '/workspace/docs/a.txt:2:foo one'
        ]);

        const insensitive = await f.ctx.explorationShell.execute('grep -rni foo /workspace/docs', f.session.value, f.context);
        assert.equal(insensitive.exitCode, 0, insensitive.stderr);
        assert.deepEqual(insensitive.stdout.trim().split('\n').sort(), [
            '/workspace/docs/a.txt:2:foo one',
            '/workspace/docs/b.txt:2:FOO two'
        ]);

        const files = await f.ctx.explorationShell.execute('grep -rl foo /workspace/docs', f.session.value, f.context);
        assert.equal(files.exitCode, 0, files.stderr);
        assert.deepEqual(files.stdout.trim().split('\n').sort(), ['/workspace/docs/a.txt']);

        const missing = await f.ctx.explorationShell.execute('grep -rn absent /workspace/docs', f.session.value, f.context);
        assert.equal(missing.exitCode, 1);
        assert.equal(missing.stdout, '');
    }
    finally { await f.close(); }
});
