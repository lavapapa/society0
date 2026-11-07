import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setup } from './b4-support.mjs';

// B10 r1：目录 ls 的 as-of 查询必须走 parent 索引，不能全表扫描 public_files。
test('B10 ls 的 as-of 查询使用 parent 索引', async () => {
    const run = await setup();
    try {
        await run.openMoment('m', 'b10-plan-open');
        const plan = await run.read(view => view.query({
            sql: 'EXPLAIN QUERY PLAN SELECT file,parent,table_path,rows,bytes,audiences,rev_from FROM public_files WHERE parent=? AND rev_from<=? AND (rev_to IS NULL OR rev_to>?) ORDER BY file LIMIT ?',
            parameters: ['/public/market/prices', 1, 1, 1100]
        }, { maxRows: 64, maxBytes: 65536 }));
        const detail = plan.map(row => String(row.detail ?? '')).join('\n');
        assert.match(detail, /SEARCH public_files/u, `未使用索引：\n${detail}`);
        assert.doesNotMatch(detail, /SCAN public_files/u, `仍在全表扫描：\n${detail}`);
    }
    finally {
        await run.close();
    }
});
