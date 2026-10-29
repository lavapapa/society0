export interface Lease<T> {
    readonly value: T;
    release(): Promise<void>;
}
export class Retention {
    private count = 0;
    retiring = false;
    private readonly drained = Promise.withResolvers<void>();
    hold(): () => Promise<void> {
        if (this.retiring)
            throw new Error('REGISTRATION_RETIRING');
        this.count++;
        let released = false;
        return async () => {
            if (released)
                return;
            released = true;
            this.count--;
            if (this.retiring && !this.count)
                this.drained.resolve();
        };
    }
    retire(): Promise<void> {
        this.retiring = true;
        if (!this.count)
            this.drained.resolve();
        return this.drained.promise;
    }
    get leases() { return this.count; }
}
export function immutableItems<T>(items: readonly {
    id: string;
    value: T;
}[]) { return Object.freeze(items.map(item => Object.freeze({ ...item }))); }
