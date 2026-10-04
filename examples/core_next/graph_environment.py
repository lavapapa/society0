"""初始化载入外部图，SQL 保存事实，NetworkX 与数值数组按当前事实重建。"""
from array import array
from contextlib import asynccontextmanager
import json
from pathlib import Path

from society0.kernel.plugins import Plugin

SCHEMA = (
    'CREATE TABLE graph_nodes(id TEXT PRIMARY KEY NOT NULL, weight REAL NOT NULL)',
    'CREATE TABLE graph_edges(source TEXT NOT NULL REFERENCES graph_nodes(id), target TEXT NOT NULL REFERENCES graph_nodes(id), PRIMARY KEY(source,target))',
)


def _projection(nodes, edges):
    """可在独立进程运行的已有派生操作；输入没有数据库或运行对象。"""
    import networkx as nx
    graph = nx.DiGraph()
    graph.add_nodes_from((key, {'weight': weight}) for key, weight in nodes)
    graph.add_edges_from(edges)
    return graph, array('d', (weight for _, weight in nodes))


class GraphEnvironment:
    def __init__(self, store, compute=None):
        self.store = store
        self.compute = compute

    def projection(self):
        nodes, edges = self.store.read(lambda reader: (
            list(reader.iter_query('SELECT id,weight FROM graph_nodes ORDER BY id')),
            list(reader.iter_query('SELECT source,target FROM graph_edges ORDER BY source,target')),
        ))
        return _projection(nodes, edges)

    async def projection_async(self):
        """明确全图投影成本；等待计算期间不持 SQLite 快照。"""
        def read(view):
            return (view.revision_for(('graph_nodes', 'graph_edges')),
                    list(view.iter_query('SELECT id,weight FROM graph_nodes ORDER BY id')),
                    list(view.iter_query('SELECT source,target FROM graph_edges ORDER BY source,target')))
        revision, nodes, edges = self.store.read(read)
        if self.compute is None:
            return _projection(nodes, edges)
        projection = await self.compute.run(_projection, nodes, edges)
        current = self.store.read(lambda view: view.revision_for(('graph_nodes', 'graph_edges')))
        if current != revision:
            raise ValueError('graph changed during projection; request a new projection')
        return projection

    def set_weight(self, node, weight):
        self.store.transaction(lambda writer: writer.execute(
            'UPDATE graph_nodes SET weight=? WHERE id=?', (weight, node)))


def graph_plugin(source, *, name='graph', compute=None):
    @asynccontextmanager
    async def prepare():
        # 文件归上下文管理器拥有；准备数据在根写入后释放。
        with Path(source).open(encoding='utf-8') as stream:
            # 初始化阶段同步读取完整图；文件与解析在同一作用域结束。
            data = json.load(stream)
            def initialize(writer):
                for node in data['nodes']:
                    writer.execute('INSERT INTO graph_nodes VALUES(?,?)', (node['id'], node['weight']))
                for edge in data['edges']:
                    writer.execute('INSERT INTO graph_edges VALUES(?,?)', tuple(edge))
            yield initialize

    def install(context):
        context.provide('graph', GraphEnvironment(context.require('storage', 'store'),
            None if compute is None else context.require(*compute)))

    requires = ('storage',) if compute is None else tuple(dict.fromkeys(('storage', compute[0])))
    return Plugin(name, requires, install, schema=SCHEMA, prepare=prepare)
