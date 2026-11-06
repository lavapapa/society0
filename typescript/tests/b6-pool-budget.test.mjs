import { test } from 'node:test';
import assert from 'node:assert/strict';
import { shellEnv } from './b6-support.mjs';
import { shellPoolMetrics } from '../.build/core/dist/exploration-shell/pool.js';

// 命令预算只约束命令本身。等 slot 与 Worker 冷启动（新建 Worker、加载 just-bash）不得占用命令超时。
test('B6 命令预算不包含排队等待与 Worker 冷启动', async () => {
    shellPoolMetrics.workersCreated = 0;
    const env = await shellEnv({ workers: 1, timeoutMs: 1000 });
    try {
        const results = await Promise.all(Array.from({ length: 10 }, () => env.ctx.explorationShell.execute('sleep 0.2', env.session.value, env.context)));
        for (const result of results) {
            assert.equal(result.exitCode, 0, result.stderr);
        }
        // 预热后已建满池，不会因为第一条命令冷启动而超时。
        assert.equal(shellPoolMetrics.workersCreated, 1);
    }
    finally {
        await env.close();
    }
});

test('B6 超时终止后立即补建 Worker，下一条命令不需要再冷启动', async () => {
    shellPoolMetrics.workersCreated = 0;
    const env = await shellEnv({ workers: 1, timeoutMs: 2000 });
    try {
        assert.equal(shellPoolMetrics.workersCreated, 1);
        await assert.rejects(env.ctx.explorationShell.execute('sleep 30', env.session.value, env.context), /SHELL_TIMEOUT/);
        // 退役与补建同步发生；补建走预热路径，而不是等下一条命令。
        assert.equal(shellPoolMetrics.workersCreated, 2);
        const after = await env.ctx.explorationShell.execute('echo after', env.session.value, env.context);
        assert.equal(after.exitCode, 0, after.stderr);
        assert.equal(after.stdout, 'after\n');
        assert.equal(shellPoolMetrics.workersCreated, 2);
        const snapshot = env.ctx.tasks.snapshot();
        assert.equal(snapshot.activeCount, 0);
        assert.equal(snapshot.pendingCount, 0);
    }
    finally {
        await env.close();
    }
});
