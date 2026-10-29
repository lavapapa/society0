import type { Context } from '@society0/host';
import '@society0/social/social-recommendations';
import type { DiscoveryScope, Invocation } from '@society0/social/contracts';
/** 外部准备由来源在短快照关闭后执行；stale交给调用者显式重排工作。 */
export async function prepareRecommendation(ctx: Context, scope: DiscoveryScope, invocation: Invocation) {
    const prepared = await ctx.recommendations.prepare(scope, {
        maxItems: 100, maxBytes: 262144
    }, invocation);
    if (prepared.status === 'stale')
        return prepared;
    const preview = await ctx.recommendations.preview(prepared.input, 'weighted/v1', {
        maxItems: 20, maxResponseBytes: 65536
    });
    return {
        status: 'ready' as const, input: prepared.input, preview
    };
}
