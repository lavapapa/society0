export type Json = null | boolean | number | string | readonly Json[] | {
    readonly [key: string]: Json;
};
export interface Invocation {
    readonly runId: string;
    readonly generation: string;
    readonly operationId: string;
    readonly signal: AbortSignal;
}
export type Disposer = () => void | Promise<void>;
export interface Lease<T> {
    readonly value: T;
    release(): Promise<void>;
}
