import { createHost, type Host } from './bootstrap.js';
import { OwnedResources } from './resources/index.js';
import { CompositionStrategies } from './composition-strategies/index.js';
import defaults from './slot-strategies/index.js';
import { Slots } from './slots/index.js';
import { ModuleResolver } from './module-resolver/index.js';
import { ConfigResolverRegistry } from './config-resolvers/index.js';
import { ProfileLoader } from './profile-loader/index.js';
import { Commands } from './commands/index.js';
import { Inspector } from './inspector/index.js';
export * from './abi.js';
export interface TestHost extends Host {
    assertQuiescent(): void;
}
export async function createTestHost(options: {
    baseURL?: string;
} = {}): Promise<TestHost> {
    const host = await createHost({ boot: [], ...options });
    try {
        await host.ctx.plugin(OwnedResources);
        await host.ctx.plugin(CompositionStrategies);
        await host.ctx.plugin(defaults);
        await host.ctx.plugin(Slots);
        await host.ctx.plugin(ModuleResolver, options);
        await host.ctx.plugin(ConfigResolverRegistry);
        await host.ctx.plugin(ProfileLoader);
        await host.ctx.plugin(Commands);
        await host.ctx.plugin(Inspector);
        return Object.assign(host, { assertQuiescent() {
                const report = host.ctx.resources.closeReport();
                if (report.opening || report.open || report.closing || report.failed)
                    throw Error(`RESOURCE_LEAK ${JSON.stringify(report.residual)}`);
            } });
    }
    catch (error) {
        await host.close();
        throw error;
    }
}
