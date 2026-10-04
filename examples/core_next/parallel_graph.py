"""可选共享计算服务：读取规范图事实，在子进程重建同一派生图。"""
import argparse
import asyncio
from pathlib import Path

from society0 import compose
from society0.plugins import compute_plugin
from examples.core_next.graph_environment import graph_plugin


async def main(source, output, *, workers, pending):
    async with compose(output, [
        compute_plugin(max_workers=workers, max_pending=pending),
        graph_plugin(source, compute=('compute', 'compute')),
    ]) as host:
        graph, weights = await host.service('graph', 'graph').projection_async()
        # 只使用父进程收到的派生结果；权威事实保持在规范存储中。
        print({'nodes': graph.number_of_nodes(), 'edges': graph.number_of_edges(),
               'weight_sum': sum(weights)})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--workers', type=int, required=True)
    parser.add_argument('--pending', type=int, required=True)
    args = parser.parse_args()
    asyncio.run(main(args.source, args.output, workers=args.workers, pending=args.pending))
