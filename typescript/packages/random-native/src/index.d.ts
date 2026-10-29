export interface RandomAddress { readonly run: string; readonly actor: string; readonly operation: string; readonly stream: string; readonly index: string; }
export interface RandomValue { readonly uint32: number; readonly uniform: number; }
export const algorithm: 'philox4x32-10/v1';
export const mapping: 'length-prefixed-utf8-seed-seq/v1';
export function at(seed: string, address: RandomAddress): RandomValue;
export function encodeAddress(seed: string, address: Omit<RandomAddress, 'index'>): Buffer;
export function philox(counter: readonly number[], key: readonly number[]): number[];
