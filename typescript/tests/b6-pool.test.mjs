import { test } from 'node:test';
import assert from 'node:assert/strict';
import { shellEnv } from './b6-support.mjs';
import { shellPoolMetrics } from '../.build/core/dist/exploration-shell/pool.js';

test('B6 worker 池复用：100 条命令不超池大小，超时只终止所在 worker', async () => {
    shellPoolMetrics.workersCreated = 0;
    const fast = await shellEnv({ workers: 2, timeoutMs: 10000 });
    let slow;
    try {
        for (let index = 0; index < 100; index++) {
            const result = await fast.ctx.explorationShell.execute('echo ok', fast.session.value, fast.context);
            assert.equal(result.exitCode, 0, result.stderr);
            assert.equal(result.stdout, 'ok\n');
        }
        assert.ok(shellPoolMetrics.workersCreated <= 2, `创建了 ${shellPoolMetrics.workersCreated} 个 Worker`);
        const before = shellPoolMetrics.workersCreated;
        slow = await shellEnv({ workers: 1, timeoutMs: 5000 });
        await assert.rejects(slow.ctx.explorationShell.execute('sleep 30', slow.session.value, slow.context), /SHELL_TIMEOUT/);
        const after = await slow.ctx.explorationShell.execute('echo after', slow.session.value, slow.context);
        assert.equal(after.exitCode, 0, after.stderr);
        assert.equal(after.stdout, 'after\n');
        assert.ok(shellPoolMetrics.workersCreated <= before + 2, `超时后共 ${shellPoolMetrics.workersCreated} 个 Worker`);
        assert.equal(slow.ctx.tasks.snapshot().activeCount, 0);
        assert.equal(slow.ctx.tasks.snapshot().pendingCount, 0);
    }
    finally {
        await fast.close();
        await slow?.close();
    }
});
