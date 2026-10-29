import type { Context } from '@society0/host';
import '@society0/social/social-measurement';
import type { RecommendationInput, Invocation } from '@society0/social/contracts';
import type { WriteLease } from '@society0/core/execution-fence';
/** 两个策略使用同一份准备后输入，结果保留来源、配置与分解分数。调用方把requiredOperations交给完成性判断。 */
export async function compareRecommendation(ctx: Context, input: RecommendationInput, lease: WriteLease, invocation: Invocation) {
    return ctx.recommendationMeasurement.compare(input, ['chronological/v1', 'weighted/v1'], lease, invocation);
}
