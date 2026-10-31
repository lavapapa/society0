const mode = process.argv[2];
const output = {
    collator: new Intl.Collator().resolvedOptions().locale,
    icu: process.versions.icu,
    full: Intl.Collator.supportedLocalesOf(['et', 'da']),
    locale: ['t', 'z', 'aa', 'b'].sort((a, b) => a.localeCompare(b))
};
if (mode === 'ordered') {
    const { ordered } = await import('../.build/host/dist/slot-strategies/index.js');
    output.ordered = ordered([{ id: 'a' }, { id: 'B' }, { id: '_x' }]);
    output.cross = ordered([{ id: 't' }, { id: 'z' }, { id: 'aa' }, { id: 'b' }]);
}
else if (mode === 'ranking') {
    const { createHost, OwnedResources, TaskSupervisor } = await import('../.build/host/dist/index.js');
    const CompositionStrategies = (await import('../.build/host/dist/composition-strategies/index.js')).default;
    const strategies = (await import('../.build/host/dist/slot-strategies/index.js')).default;
    const Slots = (await import('../.build/host/dist/slots/index.js')).default;
    const SocialRankings = (await import('../.build/social/dist/social-ranking/index.js')).default;
    const host = await createHost({ boot: [] });
    try {
        await host.ctx.plugin(OwnedResources);
        await host.ctx.plugin(TaskSupervisor, { concurrency: 8, maxBytes: 33554432 });
        await host.ctx.plugin(CompositionStrategies);
        await host.ctx.plugin(strategies);
        await host.ctx.plugin(Slots);
        await host.ctx.plugin(SocialRankings, {});
        const input = ids => ({
            id: 'i', runId: 'r',
            scope: { actorId: 'a', momentId: 'm', logicalTime: 0, query: { time: '5' } },
            versions: [], maxBytes: 65536,
            items: ids.map(id => ({ id, authorId: 'a', features: { createdTime: '5', likes: 0, replies: 0, reposts: 0 } }))
        });
        const rank = async (id, ids) => {
            const lease = await host.ctx.socialRankings.acquire(id);
            try {
                return lease.value.rank(input(ids)).map(item => item.id);
            }
            finally {
                await lease.release();
            }
        };
        const ids = ['a', 'B', '_x', 'é'];
        output.chronological = await rank('chronological/v1', ids);
        output.weighted = await rank('weighted/v1', ids);
        output.crossRanking = await rank('chronological/v1', ['t', 'z', 'aa', 'b']);
    }
    finally {
        await host.close();
    }
}
else {
    throw new Error('ORDER_LOCALE_MODE ' + mode);
}
console.log(JSON.stringify(output));
