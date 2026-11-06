import { Context, Service } from '@society0/host';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({ source: Schema.string().default('events'), maxPending: Schema.natural().min(1).default(1024), maxBytes: Schema.natural().min(1).default(1048576), leaseTimeoutMs: Schema.natural().min(1).default(60000) });
export const metadata: PluginMetadata = { "title": "proposal-events", "summary": "proposal-events", "configVersion": 1, "configPolicy": [{ "path": "/source", "mode": "new-run", "affectsResults": true }, { "path": "/maxPending", "mode": "new-run", "affectsResults": true }, { "path": "/maxBytes", "mode": "new-run", "affectsResults": true }, { "path": "/leaseTimeoutMs", "mode": "boundary", "affectsResults": false }], "scope": "generation", "hostAbi": "society0/host/1" };
export const api: ApiEntry = { "exportPath": "./proposal-events", "provides": [{ "key": "proposalSource", "protocol": "proposalSource", "major": 1, "capabilities": [] }], "requires": [{ "service": { "key": "resources", "protocol": "resources", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }] };
import type { Json, Proposal, ProposalResult, ProposalSource as Source, CompletePoint } from '../contracts/runtime.js';
import { integer } from '../clock-ticks/index.js';
export interface InputEvent {
    readonly id: string;
    readonly offset: string;
    readonly time: string;
    readonly payload: Json;
}
export class ProposalSource extends Service implements Source {
    static Config = Config;
    static inject = ['resources'];
    private events = new Map<string, {
        event: InputEvent;
        bytes: number;
        reserved: boolean;
    }>();
    private bytes = 0;
    private acknowledgedThrough = 0n;
    private reservedThrough = 0n;
    private waiters = new Set<() => void>();
    private leases = new Map<string, ReturnType<typeof setTimeout>>();
    private expired = 0;
    private ended = false;
    private config: ReturnType<typeof Config>;
    constructor(ctx: Context, config = {}) {
        super(ctx, 'proposalSource');
        this.config = Config(config);
        ctx.resources.use('proposal-events', () => {
            this.ended = true;
            for (const timer of this.leases.values())
                clearTimeout(timer);
            this.leases.clear();
            this.wake();
        });
    }
    private armLease(id: string) {
        this.clearLease(id);
        const timer = setTimeout(() => this.expireLease(id), this.config.leaseTimeoutMs);
        timer.unref();
        this.leases.set(id, timer);
    }
    private clearLease(id: string) {
        const timer = this.leases.get(id);
        if (timer) {
            clearTimeout(timer);
            this.leases.delete(id);
        }
    }
    /** reservation到期自动归还，等待者不会因持有者失联而永久挂起。 */
    private expireLease(id: string) {
        this.leases.delete(id);
        const held = this.events.get(id);
        if (!held || !held.reserved)
            return;
        held.reserved = false;
        this.expired++;
        this.wake();
    }
    private wake() {
        for (const wake of this.waiters)
            wake();
        this.waiters.clear();
    }
    async push(event: InputEvent) {
        const offset = integer(event.offset);
        integer(event.time);
        if (offset <= this.acknowledgedThrough)
            return;
        const previous = this.events.get(event.id);
        if (previous) {
            if (JSON.stringify(previous.event) !== JSON.stringify(event))
                throw Error('EVENT_ID_CONFLICT');
            return;
        }
        if (offset <= this.reservedThrough)
            throw Error('EVENT_LATE_OFFSET');
        const bytes = Buffer.byteLength(JSON.stringify(event));
        if (this.ended)
            throw Error('EVENT_CLOSED');
        if (this.events.size >= this.config.maxPending || this.bytes + bytes > this.config.maxBytes)
            throw Error('EVENT_CAPACITY');
        if ([...this.events.values()].some(x => x.event.offset === event.offset))
            throw Error('EVENT_OFFSET_CONFLICT');
        this.events.set(event.id, { event: structuredClone(event), bytes, reserved: false });
        this.bytes += bytes;
        this.wake();
    }
    end() { this.ended = true; this.wake(); }
    async waitProposal(cursor: Json, signal: AbortSignal): Promise<ProposalResult> {
        const through = integer(cursor);
        while (true) {
            signal.throwIfAborted();
            const next = [...this.events.values()].filter(x => !x.reserved && integer(x.event.offset) > through).sort((a, b) => integer(a.event.offset) < integer(b.event.offset) ? -1 : 1)[0];
            if (next) {
                next.reserved = true;
                this.reservedThrough = integer(next.event.offset);
                this.armLease(next.event.id);
                const e = next.event;
                return { kind: 'proposal', proposal: { id: e.id, cursor, planKind: 'events/1', payload: { events: [{ ...e }], nextCursor: e.offset, trigger: { time: e.time, offset: e.offset } }, reservation: { source: this.config.source, id: e.id, operationId: e.id } } };
            }
            if (this.ended)
                return { kind: 'end', reason: 'event source ended' };
            await new Promise<void>((resolve, reject) => {
                const wake = () => { signal.removeEventListener('abort', abort); resolve(); };
                const abort = () => { this.waiters.delete(wake); reject(signal.reason); };
                this.waiters.add(wake);
                signal.addEventListener('abort', abort, { once: true });
                if (signal.aborted)
                    abort();
            });
        }
    }
    async acknowledge(proposal: Proposal, cut: CompletePoint) {
        if (!cut.frontier.pendingAcknowledgements.some(x => x.source === this.config.source && x.reservationId === proposal.reservation?.id && x.proposalId === proposal.id))
            throw Error('EVENT_ACK_UNCOVERED');
        if (cut.frontier.codec !== 'events/1')
            throw Error('EVENT_ACK_CODEC');
        const through = integer(cut.frontier.value as Json);
        if (through > this.acknowledgedThrough)
            this.acknowledgedThrough = through;
        for (const [id, held] of this.events)
            if (integer(held.event.offset) <= this.acknowledgedThrough) {
                this.clearLease(id);
                this.events.delete(id);
                this.bytes -= held.bytes;
            }
    }
    diagnostics() { return { pending: this.events.size, bytes: this.bytes, waiting: this.waiters.size, expired: this.expired, acknowledgedThrough: String(this.acknowledgedThrough) }; }
    async release(proposal: Proposal) {
        this.clearLease(proposal.id);
        const held = this.events.get(proposal.id);
        if (held)
            held.reserved = false;
        this.wake();
    }
}
export default ProposalSource;
