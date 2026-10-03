"""异步载入外部图，SQL 保存事实，NetworkX 与数值数组按当前事实重建。"""
from array import array
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path

from society0.kernel.plugins import Plugin

SCHEMA = (
    'CREATE TABLE graph_nodes(id TEXT PRIMARY KEY NOT NULL, weight REAL NOT NULL)',
    'CREATE TABLE graph_edges(source TEXT NOT NULL REFERENCES graph_nodes(id), target TEXT NOT NULL REFERENCES graph_nodes(id), PRIMARY KEY(source,target))',
)


class GraphEnvironment:
    def __init__(self, store):
        self.store = store

    def projection(self):
        import networkx as nx
        nodes, edges = self.store.read(lambda reader: (
            list(reader.iter_query('SELECT id,weight FROM graph_nodes ORDER BY id')),
            list(reader.iter_query('SELECT source,target FROM graph_edges ORDER BY source,target')),
        ))
        graph = nx.DiGraph()
        graph.add_nodes_from((key, {'weight': weight}) for key, weight in nodes)
        graph.add_edges_from(edges)
        return graph, array('d', (weight for _, weight in nodes))

    def set_weight(self, node, weight):
        self.store.transaction(lambda writer: writer.execute(
            'UPDATE graph_nodes SET weight=? WHERE id=?', (weight, node)))


def graph_plugin(source, *, name='graph'):
    @asynccontextmanager
    async def prepare():
        # 文件归上下文管理器拥有；准备数据在根写入后释放。
        with Path(source).open(encoding='utf-8') as stream:
            loading = asyncio.create_task(asyncio.to_thread(json.load, stream))
            try:
                data = await asyncio.shield(loading)
            finally:
                # to_thread 的取消不会停止底层读；文件退出前等待该读结束。
                while not loading.done():
                    try:
                        await asyncio.shield(loading)
                    except asyncio.CancelledError:
                        continue
                loading.result()
            def initialize(writer):
                for node in data['nodes']:
                    writer.execute('INSERT INTO graph_nodes VALUES(?,?)', (node['id'], node['weight']))
                for edge in data['edges']:
                    writer.execute('INSERT INTO graph_edges VALUES(?,?)', tuple(edge))
            yield initialize

    def install(context):
        context.provide('graph', GraphEnvironment(context.require('storage', 'store')))

    return Plugin(name, ('storage',), install, schema=SCHEMA, prepare=prepare)
