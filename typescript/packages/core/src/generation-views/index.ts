import { Context, Service } from '@society0/host';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
export const Config = Schema.object({});
export const metadata: PluginMetadata = { "title": "generation-views", "summary": "generation-views", "configVersion": 1, "configPolicy": [], "scope": "run", "hostAbi": "society0/host/1" };
export const api: ApiEntry = { "exportPath": "./generation-views", "provides": [{ "key": "generationViews", "protocol": "generationViews", "major": 1, "capabilities": [] }], "requires": [{ "service": { "key": "resources", "protocol": "resources", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }, { "service": { "key": "executionFence", "protocol": "executionFence", "major": 1, "capabilities": [] }, "optional": false, "phase": "module" }] };
import type { GenerationView } from '../contracts/runtime.js';
import '../execution-fence/index.js';
declare module '@society0/host' {
    interface Context {
        generationViews: GenerationViews;
    }
}
export class GenerationViews extends Service {
    static Config = Config;
    static inject = ['resources', 'executionFence'];
    private selected?: {
        view: GenerationView;
        users: number;
        retired: boolean;
        done?: () => void;
    };
    constructor(ctx: Context) { super(ctx, 'generationViews'); }
    register(view: GenerationView) {
        if (this.selected)
            throw Error('GENERATION_ALREADY_BOUND');
        const record = { view: Object.freeze({ ...view }), users: 0, retired: false } as {
            view: GenerationView;
            users: number;
            retired: boolean;
            done?: () => void;
        };
        this.selected = record;
        return this.ctx.resources.use('generation-view', async () => {
            record.retired = true;
            if (record.users)
                await new Promise<void>(resolve => record.done = resolve);
            if (this.selected === record)
                this.selected = undefined;
        });
    }
    current() {
        if (!this.selected || this.selected.retired)
            throw Error('GENERATION_UNAVAILABLE');
        const { generation, controlSequence } = this.selected.view;
        return { generation, controlSequence };
    }
    async acquire(expected: string) {
        const record = this.selected;
        if (!record || record.retired || record.view.generation !== expected)
            throw Error('GENERATION_UNAVAILABLE');
        record.users++;
        let released = false;
        return { value: record.view, release: async () => {
                if (released)
                    return;
                released = true;
                if (--record.users === 0)
                    record.done?.();
            } };
    }
}
export default GenerationViews;
