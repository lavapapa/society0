"""注册表与字段驱动的 SQLite 信息提供者；查询范围由机制明确声明。"""
from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass
from urllib.parse import unquote, quote

from .interaction import DocumentChunk, Page, Query, Ref, ResourceStat, Unavailable


_ROWID = object()


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


@dataclass(frozen=True)
class DatasetSpec:
    table: str
    key: str
    columns: tuple[str, ...]
    authorize: object = None
    order_fields: tuple[str, ...] = ()
    base_count: object = None
    dependencies: tuple[str, ...] = ()
    documents: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class DocumentSpec:
    table: str
    key: str
    body: str
    authorize: object = None
    dependencies: tuple[str, ...] = ()


@dataclass(frozen=True)
class SamplePage(Page):
    population_total: int = 0


class SQLInformation:
    def bind_access_dependencies(self,tables):
        self.access_dependencies=tuple(dict.fromkeys((*self.access_dependencies,*tables)))

    def __init__(self, namespace, reader, routes, *, max_page_size=1000, access_dependencies=()):
        self.max_page_size = max_page_size
        self.namespace = namespace
        self.reader = reader
        self.access_dependencies = tuple(access_dependencies)
        self._routes = dict(routes)
        self._integer_keys = {}
        self._columns = {}
        self._order_fields = {}
        self._rowids = {}
        for route, spec in self._routes.items():
            if '/' in route or not route:
                raise ValueError('route must be one nonempty path segment')
            columns = reader.read(lambda view: view.query('PRAGMA table_info(' + _quote(spec.table) + ')'))
            info = {row[1]: row for row in columns}
            if spec.key not in info or sum(bool(row[5]) for row in columns) != 1 or not info[spec.key][5]:
                raise ValueError('dataset requires one primary key')
            key_type = info[spec.key][2].upper()
            if key_type not in ('INTEGER', 'TEXT'):
                raise ValueError('dataset primary key must be INTEGER or TEXT')
            if key_type == 'TEXT' and not info[spec.key][3]:
                raise ValueError('dataset primary key must be nonnull')
            self._integer_keys[route] = key_type == 'INTEGER'
            visible = tuple(spec.columns) if isinstance(spec, DatasetSpec) else (spec.key,)
            order_fields = tuple(spec.order_fields) if isinstance(spec, DatasetSpec) else ()
            order_fields = tuple(dict.fromkeys((*order_fields, spec.key)))
            if 'ref' in visible or any(field not in info for field in (*visible, *order_fields)):
                raise ValueError('unregistered or reserved dataset field')
            for field in order_fields:
                if not info[field][3] and not (field == spec.key and key_type == 'INTEGER'):
                    raise ValueError('ordering fields must be nonnull')
            if isinstance(spec, DocumentSpec):
                if spec.body not in info or info[spec.body][2].upper() not in ('BLOB','TEXT'):
                    raise ValueError('document body must be a BLOB or TEXT column')
                tables = reader.read(lambda view: view.query('PRAGMA table_list'))
                if next(row[4] for row in tables if row[0] == 'main' and row[1] == spec.table):
                    raise ValueError('document requires a rowid table')
                rowid = next((name for name in ('rowid', '_rowid_', 'oid') if name not in {field.casefold() for field in info}), None)
                if rowid is None:
                    raise ValueError('document requires an accessible rowid')
                self._rowids[route] = rowid
            self._columns[route] = visible
            self._order_fields[route] = order_fields
        for route,spec in self._routes.items():
            if not isinstance(spec,DatasetSpec):continue
            for column,target in spec.documents:
                document=self._routes.get(target)
                if (column not in self._columns[route] or not isinstance(document,DocumentSpec)
                    or (document.table,document.key,document.body)!=(spec.table,spec.key,column)
                    or document.authorize is not spec.authorize
                    or set(document.dependencies)!=set(spec.dependencies)):
                    raise ValueError('document mapping must share table, key, body and authorization dependencies')
                if column in self._order_fields[route]:
                    raise ValueError('document body cannot be an ordering field')

    def _projection(self,route,selected):
        spec=self._routes[route]
        documents=dict(spec.documents) if isinstance(spec,DatasetSpec) else {}
        selected=tuple(selected)
        columns=['typeof('+_quote(field)+') AS '+_quote(field) if field in documents else _quote(field) for field in selected]
        targets=[documents[field] for field in selected if field in documents]
        if targets:
            selected=(*selected,_ROWID)
            columns.append(_quote(self._rowids[targets[0]]))
        return selected,','.join(columns)

    def _route(self, path):
        parts = path.strip('/').split('/')
        if parts[0] != self.namespace or len(parts) > 3:
            raise Unavailable('resource unavailable')
        route = parts[1] if len(parts) > 1 else None
        if route is not None and route not in self._routes:
            raise Unavailable('resource unavailable')
        key = unquote(parts[2]) if len(parts) == 3 else None
        if key is not None and self._integer_keys[route]:
            try: key = int(key)
            except ValueError: raise Unavailable('resource unavailable') from None
        return route, key

    def ref(self, path):
        route, key = self._route(path)
        return Ref(self.namespace, route or 'directory', str(key) if key is not None else '')

    def _where(self, scope, spec, filters):
        clause, bindings = spec.authorize(scope) if spec.authorize else ('1', ())
        clauses, values = ['(' + clause + ')'], list(bindings)
        operators = {'eq': '=', 'ne': '!=', 'lt': '<', 'le': '<=', 'gt': '>', 'ge': '>='}
        for field, operation, value in filters:
            if field not in self._columns[self._route_name(spec)]:
                raise ValueError('filter field is not registered')
            column = _quote(field)
            if operation in ('eq', 'ne') and value is None:
                clauses.append(column + (' IS NULL' if operation == 'eq' else ' IS NOT NULL'))
            elif operation in operators:
                clauses.append(column + operators[operation] + '?')
                values.append(value)
            elif operation == 'in' and isinstance(value, (list, tuple)):
                clauses.append(column + ' IN (' + ','.join('?' for _ in value) + ')')
                values.extend(value)
            else:
                raise ValueError('filter operation is not supported')
        return ' AND '.join(clauses), values

    def _route_name(self, spec):
        return next(route for route, registered in self._routes.items() if registered is spec)

    async def list(self, scope, path, *, limit=100, cursor=None):
        route, key = self._route(path)
        if route is None:
            scope.check_active()
            items = [{'path': '/' + self.namespace + '/' + route,
                      'ref': Ref(self.namespace, route, '')} for route in self._routes]
            offset = 0 if cursor is None else cursor['offset']
            if type(offset) is not int or offset < 0:
                raise ValueError('invalid cursor offset')
            identity = [scope.actor, self.namespace]
            if cursor is not None and cursor['identity'] != identity:
                raise ValueError('cursor mismatch')
            end = min(offset + limit, len(items))
            return Page(items[offset:end], len(items), {'identity': identity, 'offset': end} if end < len(items) else None, scope.revision)
        if key is not None:
            raise Unavailable('resource unavailable')
        return await self.query(scope, path, Query(limit=limit, cursor=cursor))

    async def list_files(self,scope,path,*,limit=100,cursor=None):
        route,key=self._route(path)
        if route is None:
            page=await self.list(scope,path,limit=limit,cursor=cursor)
            return Page([dict(item,kind='directory') for item in page.items],page.total,page.next_cursor,page.revision)
        spec=self._routes.get(route)
        if spec is None or key is not None:raise Unavailable('resource unavailable')
        if type(limit) is not int or not 1<=limit<=self.max_page_size:raise ValueError('invalid file page limit')
        where,values=self._where(scope,spec,())
        identity=json.dumps([scope.actor,asdict(scope.moment),path,where,values],sort_keys=True)
        def read(view):
            revision=view.revision_for((spec.table,*spec.dependencies,*self.access_dependencies))
            if cursor is not None and (cursor['identity']!=identity or cursor['run_id']!=view.run_id or cursor['revision']!=revision):
                raise ValueError('file cursor or revision changed')
            if cursor is None:
                if isinstance(spec,DatasetSpec) and spec.base_count:
                    sql,bindings=spec.base_count(scope);counts=view.query(sql,bindings,max_rows=1);total=counts[0][0] if counts else 0
                else:total=view.query('SELECT COUNT(*) FROM '+_quote(spec.table)+' WHERE '+where,values,max_rows=1)[0][0]
            else:total=cursor['total']
            suffix='' if cursor is None else ' AND '+_quote(spec.key)+'>?'
            bindings=values if cursor is None else (*values,cursor['after'])
            rows=view.query('SELECT '+_quote(spec.key)+' FROM '+_quote(spec.table)+' WHERE '+where+suffix+' ORDER BY '+_quote(spec.key)+' LIMIT ?',(*bindings,limit+1),max_rows=limit+1)
            selected=rows[:limit]
            continuation={'identity':identity,'run_id':view.run_id,'revision':revision,'total':total,'after':selected[-1][0]} if len(rows)>limit else None
            return Page([{'path':path+'/'+quote(str(row[0]),safe=''),'ref':Ref(self.namespace,route,str(row[0])),'kind':'file'} for row in selected],total,continuation,revision)
        return self.reader.read(read,expected_revision=scope.revision)

    async def stat(self,scope,path):
        scope.check_active()
        route,key=self._route(path)
        if route is None:return ResourceStat('directory',None,scope.revision,self.ref(path))
        spec=self._routes.get(route)
        if spec is None:raise Unavailable('resource unavailable')
        if key is None:return ResourceStat('directory',None,scope.revision,self.ref(path))
        where,values=self._where(scope,spec,())
        expression=_quote(self._rowids[route]) if isinstance(spec,DocumentSpec) else 'NULL'
        def read(view):
            rows=view.query('SELECT '+expression+' FROM '+_quote(spec.table)+' WHERE '+where+' AND '+_quote(spec.key)+'=?',(*values,key),max_rows=1)
            if not rows:raise Unavailable('resource unavailable')
            total=view.read_blob(spec.table,spec.body,rows[0][0],size=0)[1] if isinstance(spec,DocumentSpec) else None
            return ResourceStat('file',total,view.revision_for((spec.table,*spec.dependencies,*self.access_dependencies)),self.ref(path))
        return self.reader.read(read,expected_revision=scope.revision)

    async def read(self, scope, path, *, offset=0, size=65536):
        scope.check_active()
        if offset < 0 or size < 1:
            raise ValueError('invalid document range')
        route, key = self._route(path)
        spec = self._routes.get(route)
        if spec is None or key is None:
            raise Unavailable('resource unavailable')
        if isinstance(spec,DatasetSpec):
            where,values=self._where(scope,spec,())
            def read_record(view):
                selected=tuple(dict.fromkeys((*spec.columns,spec.key)))
                selected,columns=self._projection(route,selected)
                rows=view.query('SELECT '+columns+' FROM '+_quote(spec.table)+' WHERE '+where+' AND '+_quote(spec.key)+'=?',(*values,key),max_rows=1)
                if not rows:raise Unavailable('resource unavailable')
                revision=view.revision_for((spec.table,*spec.dependencies,*self.access_dependencies))
                record=self._items(route,spec.columns,selected,rows,view,revision)[0]
                data=json.dumps(record,ensure_ascii=False,separators=(',',':'),default=asdict).encode()
                end=min(len(data),offset+size)
                revision=view.revision_for((spec.table,*spec.dependencies,*self.access_dependencies))
                return DocumentChunk(data[offset:end],len(data),end if end<len(data) else None,revision,self.ref(path))
            return self.reader.read(read_record,expected_revision=scope.revision)
        where, values = self._where(scope, spec, ())
        sql = ('SELECT ' + _quote(self._rowids[route]) + ' FROM ' + _quote(spec.table)
               + ' WHERE ' + where + ' AND ' + _quote(spec.key) + '=?')
        def read(view):
            rows = view.query(sql, (*values, key), max_rows=1)
            if not rows: raise Unavailable('resource unavailable')
            data, total = view.read_blob(spec.table, spec.body, rows[0][0], offset=offset, size=size)
            end = offset + len(data)
            revision=view.revision_for((spec.table,*spec.dependencies,*self.access_dependencies))
            return DocumentChunk(data, total, end if end < total else None, revision, self.ref(path))
        return self.reader.read(read, expected_revision=scope.revision)

    async def query(self, scope, path, query):
        scope.check_active()
        route, key = self._route(path)
        if route is None or key is not None:
            raise Unavailable('resource unavailable')
        if type(query.max_bytes) is not int or query.max_bytes < 512:
            raise ValueError('query byte budget must be at least 512')
        if type(query.limit) is not int or not 1 <= query.limit <= self.max_page_size:
            raise ValueError('query limit exceeds the configured page range')
        spec = self._routes[route]
        fields = tuple(query.fields) or self._columns[route]
        if any(field not in self._columns[route] for field in fields):
            raise ValueError('projection field is not registered')
        order = [tuple(item) for item in query.order] or [(spec.key, 'asc')]
        if any(field not in self._order_fields[route] or direction not in ('asc', 'desc') for field, direction in order):
            raise ValueError('order field or direction is not registered')
        if spec.key not in [field for field, _ in order]:
            order.append((spec.key, 'asc'))
        where, values = self._where(scope, spec, query.filters)
        shape = json.dumps([scope.actor, asdict(scope.moment), path, fields, query.filters, order,
                            query.sample_seed, where, values], sort_keys=True)
        table = _quote(spec.table)
        selected = tuple(dict.fromkeys((*fields, spec.key, *(field for field, _ in order))))
        selected,columns=self._projection(route,selected)
        ordering = ','.join(_quote(field) + ' ' + direction for field, direction in order)

        def read(view):
            revision=view.revision_for((spec.table,*spec.dependencies,*self.access_dependencies))
            if query.cursor is not None:
                if query.cursor['identity'] != shape or query.cursor['revision'] != revision or query.cursor['run_id'] != view.run_id:
                    raise ValueError('query cursor or revision changed')
            if isinstance(spec, DatasetSpec) and spec.base_count and not query.filters:
                sql, bindings = spec.base_count(scope)
                row = view.query(sql, bindings, max_rows=1)
                total = row[0][0] if row else 0
            else:
                total = view.query('SELECT COUNT(*) FROM ' + table + ' WHERE ' + where, values, max_rows=1)[0][0]
            if query.sample_seed is not None:
                if query.cursor is not None:
                    raise ValueError('sample has no continuation cursor')
                rng = random.Random(query.sample_seed)
                keys = []
                for number, (value,) in enumerate(view.iter_query('SELECT ' + _quote(spec.key) + ' FROM ' + table + ' WHERE ' + where + ' ORDER BY ' + _quote(spec.key), values)):
                    if number < query.limit:
                        keys.append(value)
                    else:
                        replacement = rng.randrange(number + 1)
                        if replacement < query.limit: keys[replacement] = value
                rows = []
                if keys:
                    picked = view.query('SELECT ' + columns + ' FROM ' + table + ' WHERE ' + where
                                        + ' AND ' + _quote(spec.key) + ' IN (' + ','.join('?' for _ in keys) + ')',
                                        (*values, *keys), max_rows=len(keys))
                    by_key = {row[selected.index(spec.key)]: row for row in picked}
                    rows = [by_key[key] for key in keys]
                page=SamplePage(self._items(route, fields, selected, rows,view,revision), len(rows), None, revision, total)
                if len(json.dumps(asdict(page),ensure_ascii=False,separators=(',',':')).encode())>query.max_bytes:
                    raise ValueError('sample exceeds byte budget; reduce limit or project fewer fields')
                return page
            select = 'SELECT ' + columns + ' FROM ' + table + ' WHERE ' + where
            ending = ' ORDER BY ' + ordering + ' LIMIT ?'
            sql, bindings = select + ending, [*values, query.limit + 1]
            if query.cursor is not None:
                last = query.cursor['last']
                if len(last) != len(order): raise ValueError('invalid query cursor')
                # 互斥键范围各取一页；原生 UNION ALL 合并有界候选。
                branches, bindings = [], []
                for index, (field, direction) in enumerate(order):
                    equal = [_quote(order[i][0]) + '=?' for i in range(index)]
                    greater = _quote(field) + ('>?' if direction == 'asc' else '<?')
                    predicate = ' AND '.join((*equal, greater))
                    branches.append('SELECT * FROM (' + select + ' AND ' + predicate + ending + ')')
                    bindings.extend((*values, *last[:index + 1], query.limit + 1))
                sql = ' UNION ALL '.join(branches) + ending
                bindings.append(query.limit + 1)
            items=[];last=None;used=0;more=False
            def continuation(tail):
                return {'identity':shape,'revision':revision,'run_id':view.run_id,
                        'last':[tail[field] for field,_ in order]}
            for row in view.iter_query(sql,bindings):
                if len(items)==query.limit:
                    more=True;break
                item=self._items(route,fields,selected,[row],view,revision)[0]
                tail=dict(zip(selected,row))
                candidate=continuation(tail)
                overhead=len(json.dumps(asdict(Page([],total,candidate,revision)),ensure_ascii=False,separators=(',',':')).encode())
                size=len(json.dumps(item,ensure_ascii=False,separators=(',',':'),default=asdict).encode())
                if overhead+used+size+len(items)>query.max_bytes:
                    if not items:raise ValueError('query byte budget cannot hold row metadata; project fields or declare document columns')
                    more=True;break
                items.append(item);used+=size;last=tail
            cursor=continuation(last) if more else None
            return Page(items,total,cursor,revision)
        return self.reader.read(read, expected_revision=scope.revision)

    def _items(self, route, fields, selected, rows, view=None, revision=None):
        key = self._routes[route].key
        items = []
        for row in rows:
            values = dict(zip(selected, row))
            item = {field: values[field] for field in fields}
            spec=self._routes[route]
            for field,target in (spec.documents if isinstance(spec,DatasetSpec) else ()):
                if field not in fields:continue
                if values[field]=='null':
                    item[field]=None
                    continue
                _,total=view.read_blob(spec.table,field,values[_ROWID],size=0)
                path='/'+self.namespace+'/'+target+'/'+quote(str(values[key]),safe='')
                item[field]={'kind':'document_ref','path':path,'total_bytes':total,'ref':self.ref(path),'expected_revision':revision}
            item['ref'] = Ref(self.namespace, route, str(values[key]))
            items.append(item)
        return items
