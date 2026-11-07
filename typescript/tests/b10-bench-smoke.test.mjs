import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { runBench, REQUIRED_REPORT_FIELDS, REQUIRED_OPERATIONS } from '../bench/public-vfs.mjs';

// B10：最终实现（/public + /self）的小规模冒烟，固定报告字段与历史曲线结构。
test('B10 基准冒烟：/public 模式生成字段齐全的最终报告', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'b10-bench-smoke-'));
    try {
        const report = await runBench({
            mode: 'public',
            directory,
            actors: 10, files: 100, rowsPerFile: 4, concurrency: 8,
            moments: 3, changeRatio: 0.01,
            samples: { ls: 3, stat: 5, read: 3, find: 1, grep: 1, bashStart: 2 },
            checkpoints: [1, 3]
        });
        for (const field of REQUIRED_REPORT_FIELDS)
            assert.ok(field in report, `report.${field} 缺失`);
        for (const name of REQUIRED_OPERATIONS) {
            const operation = report.operations[name];
            assert.ok(operation, `operations.${name} 缺失`);
            for (const field of ['samples', 'p50Ms', 'p95Ms', 'p99Ms', 'errors'])
                assert.ok(field in operation, `operations.${name}.${field} 缺失`);
        }
        assert.equal(report.mode, 'public');
        assert.equal(report.scale.actors, 10);
        assert.equal(report.scale.files, 100);
        assert.equal(report.scale.moments, 3);
        assert.ok(report.operations.find.extra.nodes >= 100);
        assert.ok(report.operations.concurrent.extra.throughputPerSecond > 0);
        assert.ok(Array.isArray(report.history.checkpoints) && report.history.checkpoints.length === 2);
        for (const point of report.history.checkpoints) {
            assert.ok(typeof point.moment === 'number', 'history checkpoint moment 缺失');
            assert.ok(point.operations.ls.p50Ms >= 0, 'history checkpoint ls 缺失');
            assert.ok(point.operations.grep.p50Ms >= 0, 'history checkpoint grep 缺失');
        }
        for (const field of ['mainBytes', 'walBytes', 'totalBytes'])
            assert.ok(field in report.database, `database.${field} 缺失`);
        for (const field of ['peakRssBytes', 'maxRssBytes', 'loadAverage'])
            assert.ok(field in report.memory, `memory.${field} 缺失`);
        for (const field of ['renderCount', 'cacheHitRate', 'rgProcesses', 'snapshotOpens', 'readerQueueWaitMs', 'sqliteSnapshotBudgetErrors'])
            assert.ok(field in report.counters, `counters.${field} 缺失`);
        assert.ok(report.coldRender.bytes > 0);
        assert.ok(report.write1000.ms >= 0);
        assert.ok(report.share.renders >= 1);
        assert.ok(report.operations.selfRead.samples >= 1);
    } finally {
        await rm(directory, { recursive: true, force: true });
    }
});
