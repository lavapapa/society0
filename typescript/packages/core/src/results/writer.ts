import type { Context } from '@society0/cordis';
import type { WriteLease } from '../execution-fence/index.js';
import type { Json } from '../contracts/common.js';
import type { StoredValue, ResultWriteBounds, ResultSetDescription } from '../contracts/data.js';
import type { TransactionRequest } from '../sqlite/contracts.js';
import { fail, bytes } from '../information/page.js';
import { prepareValue, attachValue } from './payload.js';
import '../sqlite/index.js';
import '../artifacts-local/index.js';
interface PreparedRow {
    payload: StoredValue;
    encoded: string;
    size: number;
    capture: number;
}
/** 单遍消费器：一批成功后才推进内存head；EOF单独提交finished。 */
export class ResultWriter {
    private head = 0;
    private batch = 0;
    private pending: PreparedRow[] = [];
    private input = 0;
    private captured = 0;
    private maxRows: number;
    private maxInput: number;
    constructor(private ctx: Context, private lease: WriteLease, private operationId: string, private id: string, private limits: ResultWriteBounds, private signal: AbortSignal, options: {
        batchRows: number;
        batchBytes: number;
    }) {
        for (const value of Object.values(limits))
            if (!Number.isSafeInteger(value) || value < 1)
                fail('RESULT_BUDGET');
        this.maxRows = Math.min(limits.maxBatchRows, options.batchRows);
        this.maxInput = Math.min(limits.maxInputBytes, options.batchBytes);
    }
    private request(suffix: string): TransactionRequest { return { operationId: this.operationId + ':' + suffix, maxDurationMs: 15000, maxStatements: 4 + this.maxRows * 2, maxInputBytes: this.limits.maxInputBytes, maxChangedRows: 4 + this.maxRows * 2, maxCapturedBytes: this.limits.maxCapturedBytes }; }
    private async prepare(row: Json): Promise<PreparedRow> {
        this.signal.throwIfAborted();
        if (bytes(row) > this.limits.maxRecordBytes)
            return fail('RESULT_RECORD_BUDGET');
        const payload = await prepareValue(row, Math.min(16384, Math.floor(this.maxInput / 4), Math.floor(this.limits.maxCapturedBytes / 8)), this.ctx.artifacts, this.signal), encoded = JSON.stringify(payload), size = Buffer.byteLength(encoded) + Buffer.byteLength(this.id) + 128, capture = size * 2 + 256;
        if (size > this.maxInput || capture > this.limits.maxCapturedBytes)
            return fail('RESULT_BATCH_BUDGET');
        return { payload, encoded, size, capture };
    }
    private full(row: PreparedRow) { return this.pending.length >= this.maxRows || this.input + row.size > this.maxInput || this.captured + row.capture > this.limits.maxCapturedBytes; }
    private async append(row: PreparedRow) {
        if (this.full(row))
            await this.flush();
        this.pending.push(row);
        this.input += row.size;
        this.captured += row.capture;
        if (this.pending.length === this.maxRows)
            await this.flush();
    }
    private async flush() {
        if (!this.pending.length)
            return;
        this.signal.throwIfAborted();
        const current = this.pending;
        await this.ctx.database.transaction(this.lease, this.request('batch-' + (++this.batch)), async (tx) => {
            for (const value of current)
                await attachValue(tx, value.payload, this.ctx.artifacts);
            await tx.executeMany('INSERT INTO result_records VALUES(?,?,?)', current.map((value, i) => [this.id, this.head + i + 1, value.encoded]), { maxRows: this.maxRows, maxInputBytes: this.limits.maxInputBytes });
            await tx.execute({ sql: 'UPDATE result_sets SET head=? WHERE id=?', parameters: [this.head + current.length, this.id] });
        });
        this.head += current.length;
        this.pending = [];
        this.input = 0;
        this.captured = 0;
    }
    async write(rows: AsyncIterable<Json>): Promise<ResultSetDescription> {
        this.signal.throwIfAborted();
        await this.ctx.database.transaction(this.lease, this.request('create'), tx => tx.execute({ sql: 'INSERT INTO result_sets VALUES(?,0,0)', parameters: [this.id] }));
        for await (const row of rows)
            await this.append(await this.prepare(row));
        await this.flush();
        this.signal.throwIfAborted();
        await this.ctx.database.transaction(this.lease, this.request('finish'), tx => tx.execute({ sql: 'UPDATE result_sets SET finished=1 WHERE id=?', parameters: [this.id] }));
        return { id: this.id, through: String(this.head), records: String(this.head), finished: true };
    }
}
