import { setup, scope } from './pr07-support.mjs';
import ExplorationShell from '../.build/core/dist/exploration-shell/index.js';

export async function shellEnv(options = {}) {
    const env = await setup();
    await env.host.ctx.plugin(ExplorationShell, { timeoutMs: 120000, ...options });
    let ctx;
    await env.host.ctx.plugin({ inject: ['explorationShell', 'exploration', 'database', 'tasks'], apply(c) { ctx = c; } });
    const base = env.context('b6-' + Math.random().toString(36).slice(2));
    const context = { ...base, transaction: (request, body) => ctx.database.transaction(base.writeLease, request, body) };
    const session = await ctx.exploration.acquire(scope, context);
    return { env, ctx, context, session, close: async () => { try { await session.release(); } catch {} await env.close(); } };
}
