"""批量不可变资料：机制拥有授权和热引用，正文按页及字节范围读取。"""
from dataclasses import asdict
import json

from society0.kernel.datasets import Datasets
from society0.kernel.interaction import DocumentChunk, Page, Ref, Unavailable
from society0.kernel.plugins import Plugin

SCHEMA = ('CREATE TABLE catalog_head(id INTEGER PRIMARY KEY,owner TEXT NOT NULL,dataset TEXT NOT NULL)',)


class Catalog:
    def __init__(self, store, datasets=None):
        self.store = store
        self.datasets = Datasets(store) if datasets is None else datasets

    def replace(self, owner, rows):
        return self.datasets.import_rows('catalog', rows, attach=lambda writer, ref:
            writer.execute('INSERT INTO catalog_head VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET owner=excluded.owner,dataset=excluded.dataset',
                           (owner,json.dumps(ref))))

    def ref(self, path):
        return Ref('catalog', 'dataset' if path=='/catalog' else 'row', path)

    def _current(self, scope):
        scope.check_active()
        row = self.store.read(lambda view: view.query('SELECT dataset FROM catalog_head WHERE id=1 AND owner=?', (scope.actor,),max_rows=1))
        if not row: raise Unavailable('catalog unavailable')
        return json.loads(row[0][0])

    async def query(self, scope, path, query):
        if path!='/catalog' or query.fields or query.filters or query.order or query.sample_seed is not None:
            raise ValueError('catalog exposes original ordered rows')
        ref = self._current(scope)
        identity = [scope.actor, asdict(scope.moment), ref]
        cursor=query.cursor
        if cursor is not None and cursor['identity']!=identity:raise ValueError('catalog cursor mismatch')
        page=self.datasets.page(ref,cursor=None if cursor is None else cursor['page'],limit=query.limit)
        for item in page['items']:
            item['ref']=self.ref('/catalog/'+str(item['ordinal']))
        cursor=None if page['next_cursor'] is None else {'identity':identity,'page':page['next_cursor']}
        return Page(page['items'],page['total'],cursor,ref['id'])

    async def read(self,scope,path,*,offset=0,size=65536):
        ref=self._current(scope)
        prefix,ordinal=path.rsplit('/',1)
        if prefix!='/catalog':raise Unavailable('catalog row unavailable')
        part=self.datasets.read_payload(ref,int(ordinal),offset=offset,size=size)
        return DocumentChunk(part['data'],part['total_bytes'],part['next_offset'],ref['id'],self.ref(path))


def catalog_plugin():
    def install(context):
        context.provide('catalog', Catalog(context.require('storage','store'),context.require('datasets','datasets')))
    return Plugin('catalog',('storage','datasets'),install,schema=SCHEMA)
