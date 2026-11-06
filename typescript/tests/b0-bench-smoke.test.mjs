import { test } from 'node:test';
import assert from 'node:assert/strict';
import { runBench, REQUIRED_REPORT_FIELDS, REQUIRED_OPERATIONS } from '../bench/public-vfs.mjs';

test('B0 基准冒烟：小规模生成字段齐全的现状基线报告', async () => {
    const report = await runBench({
        actors: 10, files: 100, rowsPerFile: 4, concurrency: 8,
        samples: { ls: 3, stat: 5, read: 3, find: 1, grep: 1, bashStart: 2 }
    });
    for (const field of REQUIRED_REPORT_FIELDS)
        assert.ok(field in report, `report.${field} 缺失`);
    for (const name of REQUIRED_OPERATIONS) {
        const operation = report.operations[name];
        assert.ok(operation, `operations.${name} 缺失`);
        for (const field of ['samples', 'p50Ms', 'p95Ms', 'p99Ms', 'errors'])
            assert.ok(field in operation, `operations.${name}.${field} 缺失`);
    }
    assert.equal(report.mode, 'current');
    assert.equal(report.scale.actors, 10);
    assert.equal(report.scale.files, 100);
    assert.ok(report.operations.find.samples >= 1);
    assert.ok(report.operations.find.extra.nodes > 0);
    for (const field of ['renderCount', 'cacheHitRate', 'rgProcesses', 'snapshotOpens', 'readerQueueWaitMs'])
        assert.ok(field in report.counters, `counters.${field} 缺失`);
});
