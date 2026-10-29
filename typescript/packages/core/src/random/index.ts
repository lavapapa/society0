import { Context, Service } from '@society0/host';
import Schema from '@society0/schemastery';
import type { PluginMetadata, ApiEntry } from '@society0/host/module-resolver';
import { at, encodeAddress } from '@society0/random-native';
import type { Random, RandomAddress, RandomValue } from '../contracts/random.js';
export type { Random, RandomAddress, RandomValue } from '../contracts/random.js';
export interface RandomConfig { algorithm: 'philox4x32-10/v1'; seed: string; }
export const Config = Schema.object({
    algorithm: Schema.const('philox4x32-10/v1').default('philox4x32-10/v1'),
    seed: Schema.string().default('study-1'),
});
export const metadata: PluginMetadata = {
    title: 'random', summary: 'Addressable Philox4x32-10 scientific random values',
    configVersion: 1, scope: 'generation', allowedScopes: ['generation', 'bootstrap'], hostAbi: 'society0/host/1',
    configPolicy: [
        { path: '/algorithm', mode: 'new-run', affectsResults: true },
        { path: '/seed', mode: 'new-run', affectsResults: true },
    ],
};
export const api: ApiEntry = { exportPath: './random', provides: [{ key: 'random', protocol: 'random', major: 1, capabilities: [] }], requires: [] };
declare module '@society0/host' { interface Context { random: Random; } }
export class RandomStreams extends Service implements Random {
    static Config = Config;
    private readonly seed: string;
    constructor(ctx: Context, config: RandomConfig) {
        super(ctx, 'random');
        if (config.algorithm !== 'philox4x32-10/v1') throw new TypeError('RANDOM_ALGORITHM');
        encodeAddress(config.seed, { run: '', actor: '', operation: '', stream: '' });
        this.seed = config.seed;
    }
    describe() { return { algorithm: 'philox4x32-10/v1' as const, mapping: 'length-prefixed-utf8-seed-seq/v1' as const, seed: this.seed }; }
    at(address: RandomAddress): RandomValue { return at(this.seed, address); }
}
export default RandomStreams;
