// cases: D31 memory.write threadId 容错；默认 1 MiB/10000 行限制下 public 大目录与大文件可查询；shell 超限可读
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setup as publicSetup } from './b4-support.mjs';
import { setup as memorySetup } from './pr09-support.mjs';
import Memory from '../.build/extras/dist/memory/index.js';
import ExplorationShell from '../.build/core/dist/exploration-shell/index.js';

const decl = {
    path: '/public/econ/directory', title: '目录', description: 'D31 大目录',
    schema: { type: 'object', properties: { id: { type: 'string' }, file: { type: 'string' }, note: { type: 'string' } } },
    key: ['id'], sort: [{ field: 'file' }, { field: 'id' }], shard: { by: 'file', format: 'value' }
};

async function withMemory(f, operationId, body) {
    const reservation = await f.ctx.executionFence.reserve({ id: operationId });
    try {
        const context = {
            ctx: f.ctx, runId: f.runId, generation: 'g', operationId,
            signal: new AbortController().signal, actorId: 'a', momentId: 'm', logicalTime: 0,
            database: f.ctx.database, writeLease: reservation.lease
        };
        return await body(context);
    }
    finally {
        await reservation.release();
    }
}

test('D31 memory.write 省略 threadId 落到当前激活 thread，正确 id 成功，错误 id 明确拒绝', async () => {
    const f = await memorySetup();
    try {
        await f.ctx.plugin(Memory);
        await withMemory(f, 'd31-memory-omitted', async context => {
            const receipt = await f.ctx.actions.execute('memory.write', { body: { id: 'omitted' } }, context);
            assert.equal(receipt.status, 'completed');
            const stored = await f.read(view => f.ctx.memoryRepository.read(view, 'd31-memory-omitted'));
            assert.equal(stored?.threadId, f.threadId);
        });
        await withMemory(f, 'd31-memory-correct', async context => {
            const receipt = await f.ctx.actions.execute('memory.write', { threadId: f.threadId, body: { id: 'correct' } }, context);
            assert.equal(receipt.status, 'completed');
            const stored = await f.read(view => f.ctx.memoryRepository.read(view, 'd31-memory-correct'));
            assert.equal(stored?.threadId, f.threadId);
        });
        await withMemory(f, 'd31-memory-wrong', async context => {
            const receipt = await f.ctx.actions.execute('memory.write', { threadId: 'made-up-by-agent', body: { id: 'wrong' } }, context);
            assert.equal(receipt.status, 'rejected');
            assert.equal(receipt.value.code, 'MEMORY_THREAD_UNKNOWN');
            assert.match(receipt.value.message, /你传了 made-up-by-agent/);
            assert.ok(receipt.value.message.includes(f.threadId), '拒绝消息应列出当前可用 threadId');
            assert.equal(await f.read(view => f.ctx.memoryRepository.read(view, 'd31-memory-wrong')), undefined);
        });
    }
    finally {
        await f.close();
    }
});

test('D31 默认 1 MiB/10000 行限制下 public 大目录、大文件、find/grep/head/cat 与 shell 截断均可读', async () => {
    const run = await publicSetup({ tables: [decl], maxQueryBytes: 1048576, maxQueryRows: 10000 });
    try {
        const bigRows = Array.from({ length: 3200 }, (_, index) => ({ id: 'big-' + String(index).padStart(4, '0'), file: 'big', note: 'x'.repeat(480) + (index === 0 ? ' needle' : '') }));
        await run.tx('d31-seed-big', tx => run.ctx.public.writer(tx).upsert(decl.path, bigRows));
        const total = 10050;
        await run.tx('d31-seed-dir', async tx => {
            const rev = BigInt(tx.baseRevision) + 1n;
            const rows = Array.from({ length: total }, (_, index) => {
                const name = 'f' + String(index).padStart(5, '0');
                return [decl.path + '/' + name + '.jsonl', decl.path, rev, null, decl.path, 1, 2, '["all"]', name + '.jsonl', 'jsonl'];
            });
            await tx.executeMany('INSERT INTO public_files(file,parent,rev_from,rev_to,table_path,rows,bytes,audiences,basename,extension) VALUES(?,?,?,?,?,?,?,?,?,?)', rows, { maxRows: total, maxInputBytes: 8 * 1024 * 1024 });
            const hugeFile = '/public/econ/huge.jsonl', huge = JSON.stringify({ id: 'huge', file: 'huge', note: 'y'.repeat(1200000) });
            await tx.executeMany('INSERT INTO public_files(file,parent,rev_from,rev_to,table_path,rows,bytes,audiences,basename,extension) VALUES(?,?,?,?,?,?,?,?,?,?)', [[hugeFile, '/public/econ', rev, null, decl.path, 1, Buffer.byteLength(huge) + 1, '["all"]', 'huge.jsonl', 'jsonl']], { maxRows: 1, maxInputBytes: 8 * 1024 * 1024 });
            await tx.executeMany('INSERT INTO public_rows(file,row_key,sort_key,rev_from,rev_to,audience,line) VALUES(?,?,?,?,NULL,?,?)', [[hugeFile, 'huge', 'huge', rev, 'all', Buffer.from(huge)]], { maxRows: 1, maxInputBytes: 8 * 1024 * 1024 });
        });
        await run.openMoment('m', 'd31-open');
        const base = run.acquisition('a', 'm', 'd31-explore');
        const context = { ...base, actionRegistrationId: 'd31-shell', transaction: (request, body) => run.ctx.database.transaction(base.writeLease, request, body) };
        const session = await run.ctx.exploration.acquire(run.scope('a', 'm'), context);
        try {
            const found = await session.value.find({ root: decl.path, glob: '*.jsonl', page: { maxItems: total + 16, maxResponseBytes: 67108864 } });
            assert.equal(found.total, String(total + 1));
            assert.equal(found.items.length, total + 1);
            const grep = await session.value.grep({ root: decl.path, pattern: 'needle', mode: 'literal', page: { maxItems: 64, maxResponseBytes: 1048576 } });
            assert.ok(grep.items.length >= 1);
            assert.equal(grep.items[0].path, decl.path + '/big.jsonl');

            await run.host.ctx.plugin(ExplorationShell, { timeoutMs: 120000, maxFileBytes: 1048576, maxOutputBytes: 4194304, maxRpcBytes: 8388608 });
            let shell;
            await run.host.ctx.plugin({ inject: ['explorationShell', 'exploration', 'tasks', 'database'], apply(c) { shell = c; } });
            const head = await shell.explorationShell.execute('head -n 20 /public/econ/directory/big.jsonl', session.value, context);
            assert.equal(head.exitCode, 0);
            assert.equal(head.stdout.trim().split('\n').length, 20);
            const cat = await shell.explorationShell.execute('cat /public/econ/directory/big.jsonl', session.value, context);
            assert.equal(cat.exitCode, 0);
            assert.equal(cat.stdout.length, 1048576 - 65536);
            assert.match(cat.stderr, /已截断/);
            assert.match(cat.stderr, /head\/grep/);
            const budget = await shell.explorationShell.execute('cat /public/econ/huge.jsonl', session.value, context);
            assert.notEqual(budget.exitCode, 0);
            assert.match(budget.stderr, /单次查询预算/);
            assert.match(budget.stderr, /head\/grep/);
        }
        finally {
            await session.release();
        }
    }
    finally {
        await run.close();
    }
});
