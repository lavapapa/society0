/** JSON-safe address. index is canonical decimal uint64; run is an explicit address component. */
export interface RandomAddress {
    readonly run: string;
    readonly actor: string;
    readonly operation: string;
    readonly stream: string;
    readonly index: string;
}
export interface RandomValue { readonly uint32: number; readonly uniform: number; }
export interface Random { at(address: RandomAddress): RandomValue; }
