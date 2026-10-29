import { slotKey } from '@society0/host/slots';
import type { Json } from '@society0/core/contracts/common';
import type { DatasetCodec as BaseCodec } from '@society0/core/contracts/data';
export interface DatasetCodec extends BaseCodec {
    encode(rows: AsyncIterable<Json>, options?: {
        maxRecordBytes: number;
    }): AsyncIterable<Uint8Array>;
}
export const datasetCodec = slotKey<DatasetCodec>('society/dataset-codec', 1);
export function datasetFault(code: string): never { throw Object.assign(new Error(code), { code }); }
export function validateCodec(value: unknown): DatasetCodec { if (isCodec(value))
    return value; return datasetFault('DATASET_CODEC'); }
function isCodec(value: unknown): value is DatasetCodec { return typeof value === 'object' && value !== null && 'id' in value && typeof value.id === 'string' && 'encode' in value && typeof value.encode === 'function' && 'decode' in value && typeof value.decode === 'function'; }
