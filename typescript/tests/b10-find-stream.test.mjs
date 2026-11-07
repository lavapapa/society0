import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setup } from './b4-support.mjs';

// B10 r1：find 不得把 1 万条聚合结果整体 JSON.parse；大结果应直接按行返回。
test('B10 find 大结果不整体 JSON.parse 聚合字符串', async () => {
    const run = await setup();
    const total = 6000;
    const parent = '/public/bulk';
    try {
        await run.tx('b10-find-seed', async tx => {
            const rev = BigInt(tx.baseRevision) + 1n;
            const rows = Array.from({ length: total }, (_, index) => {
                const name = 'f' + String(index).padStart(5, '0');
                return [parent + '/' + name + '.jsonl', parent, rev, null, parent, 1, 64, '["all"]', name + '.jsonl', 'jsonl'];
            });
            await tx.executeMany('INSERT INTO public_files(file,parent,rev_from,rev_to,table_path,rows,bytes,audiences,basename,extension) VALUES(?,?,?,?,?,?,?,?,?,?)', rows, { maxRows: total, maxInputBytes: 8 * 1024 * 1024 });
        });
        await run.openMoment('m', 'b10-find-open');
        const session = await run.ctx.exploration.acquire(run.scope('a', 'm'), run.acquisition('a', 'm', 'b10-find'));
        const original = JSON.parse;
        let bulk;
        JSON.parse = function (text, ...rest) {
            if (typeof text === 'string' && text.length > 200000) {
                bulk = text.length;
                throw Error('BULK_JSON_PARSE');
            }
            return original(text, ...rest);
        };
        let page;
        try {
            page = await session.value.find({ root: parent, glob: '*.jsonl', page: { maxItems: total + 16, maxResponseBytes: 8 * 1024 * 1024 } });
        }
        finally {
            JSON.parse = original;
            await session.release();
        }
        assert.equal(bulk, undefined, `find 整体解析了 ${bulk} 字节的 JSON 聚合`);
        assert.equal(page.items.length, total);
    }
    finally {
        await run.close();
    }
});
