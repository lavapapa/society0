"""主体私有文件索引；正文按变更文件保存，原生 OverlayFs 负责文件操作。"""
from __future__ import annotations

import asyncio
from pathlib import PurePosixPath

from .plugins import Plugin
from .interaction import DocumentChunk, Page, Ref

WORKSPACE_SCHEMA=(
    'CREATE TABLE workspace_heads(actor TEXT PRIMARY KEY NOT NULL,state BLOB NOT NULL,FOREIGN KEY(actor) REFERENCES actors(id))',
    'CREATE TABLE workspace_files(actor TEXT NOT NULL,path TEXT NOT NULL,parent TEXT NOT NULL,kind TEXT NOT NULL,mode INTEGER NOT NULL,modified_ns INTEGER NOT NULL,created_ns INTEGER NOT NULL,target TEXT,artifact TEXT,size INTEGER NOT NULL,PRIMARY KEY(actor,path),FOREIGN KEY(actor) REFERENCES actors(id))',
    'CREATE INDEX workspace_directory ON workspace_files(actor,parent,path)',
    'CREATE TABLE workspace_counts(actor TEXT NOT NULL,parent TEXT NOT NULL,total INTEGER NOT NULL,PRIMARY KEY(actor,parent),FOREIGN KEY(actor) REFERENCES actors(id))',
)


class WorkspaceStore:
    def __init__(self,store):
        store.read(lambda view:view.query('SELECT id FROM actors LIMIT 0'))
        self.store=store

    def open(self,scope):
        scope.check_active()
        def read(view):
            if not view.query('SELECT 1 FROM actors WHERE id=?',(scope.actor,)):raise KeyError(scope.actor)
            rows=view.query('SELECT state FROM workspace_heads WHERE actor=?',(scope.actor,))
            root=view.query('SELECT mode,modified_ns FROM workspace_files WHERE actor=? AND path=?',(scope.actor,'/'))
            return (rows[0][0] if rows else None), (root[0] if root else (0o755,0))
        state,root=self.store.read(read)
        return WorkspaceLease(self,scope,state,root)


class WorkspaceLease:
    def __init__(self,owner,scope,state,root):
        self.owner=owner;self.scope=scope;self.state=state;self.root=root
        self._closed=False;self._tasks=set()

    def _check(self):
        if self._closed:raise RuntimeError('workspace lease is closed')
        self.scope.check_active()

    async def callback(self,operation,path):
        task=asyncio.current_task();self._tasks.add(task)
        try:
            self._check()
            def read(view):
                if operation=='list':
                    return [(PurePosixPath(p).name,k,size,mode,modified,created) for p,k,size,mode,modified,created in view.iter_query(
                        'SELECT path,kind,size,mode,modified_ns,created_ns FROM workspace_files WHERE actor=? AND parent=? ORDER BY path',(self.scope.actor,path))]
                rows=view.query('SELECT kind,size,mode,modified_ns,created_ns,target,artifact FROM workspace_files WHERE actor=? AND path=?',(self.scope.actor,path))
                if not rows:
                    if path=='/':row=('directory',0,0o755,0,0,None,None)
                    elif operation=='exists':return False
                    else:raise FileNotFoundError(path)
                else:row=rows[0]
                if operation=='exists':return True
                if operation=='stat':return row[:5]
                if operation=='read_link':
                    if row[0]!='symlink':raise ValueError('not a symbolic link')
                    return row[5]
                if operation=='read':
                    if row[0]=='directory':raise IsADirectoryError(path)
                    # Bashkit 原生链接保持 inert，readlink 可取原目标。
                    if row[0]=='symlink':raise FileNotFoundError(path)
                    return self.owner.store.read_artifact(row[6],size=max(1,row[1]))[0]
                raise ValueError('unknown filesystem operation')
            result=self.owner.store.read(read)
            self._check();return result
        finally:self._tasks.discard(task)

    async def read_range(self,path,*,offset=0,size=65536,expected_revision=None):
        self._check()
        if offset<0 or size<1:raise ValueError('invalid workspace byte range')
        def read(view):
            rows=view.query('SELECT kind,artifact,size FROM workspace_files WHERE actor=? AND path=?',(self.scope.actor,path))
            if not rows:raise FileNotFoundError(path)
            kind,artifact,total=rows[0]
            if kind=='directory':raise IsADirectoryError(path)
            if kind!='file':raise FileNotFoundError(path)
            revision=view.revision_for(('workspace_files',))
            if expected_revision is not None and expected_revision!=revision:raise ValueError('workspace revision changed')
            data,_=self.owner.store.read_artifact(artifact,offset=offset,size=size)
            end=offset+len(data)
            return DocumentChunk(data,total,end if end<total else None,revision,Ref('workspace','file',path))
        value=self.owner.store.read(read)
        self._check()
        return value

    async def list_files(self,path,*,limit=100,cursor=None):
        self._check()
        if type(limit) is not int or limit<1:raise ValueError('invalid workspace page limit')
        def read(view):
            revision=view.revision_for(('workspace_files',))
            if cursor is not None and (cursor['revision']!=revision or cursor['actor']!=self.scope.actor or cursor['path']!=path):raise ValueError('workspace cursor changed')
            counts=view.query('SELECT total FROM workspace_counts WHERE actor=? AND parent=?',(self.scope.actor,path))
            total=counts[0][0] if counts else 0
            after='' if cursor is None else cursor['after']
            rows=view.query('SELECT path,kind,size FROM workspace_files WHERE actor=? AND parent=? AND path>? ORDER BY path LIMIT ?',(self.scope.actor,path,after,limit+1),max_rows=limit+1)
            selected=rows[:limit]
            next_cursor={'revision':revision,'actor':self.scope.actor,'path':path,'after':selected[-1][0]} if len(rows)>limit else None
            return Page([{'path':p,'kind':k,'total_bytes':size} for p,k,size in selected],total,next_cursor,revision)
        result=self.owner.store.read(read)
        self._check()
        return result

    def save(self,state,changes):
        self._check()
        entries=[]
        for entry in changes['entries']:
            entry=dict(entry)
            if entry['kind']=='fifo':raise ValueError('persistent named pipes are not supported')
            body=entry.pop('content',None)
            entry['artifact']=self.owner.store.prepare_artifact((body,)) if body is not None else None
            entry['size']=len(body) if body is not None else len(entry.get('target','').encode())
            entries.append(entry)
        def write(writer):
            for path in changes['removed']:
                counts=writer.query('SELECT parent,COUNT(*) FROM workspace_files WHERE actor=? AND (path=? OR (path>=? AND path<?)) GROUP BY parent',
                    (self.scope.actor,path,path+'/',path+'0'))
                for parent,total in counts:
                    writer.execute('UPDATE workspace_counts SET total=total-? WHERE actor=? AND parent=?',(total,self.scope.actor,parent))
                writer.execute('DELETE FROM workspace_files WHERE actor=? AND (path=? OR (path>=? AND path<?))',
                    (self.scope.actor,path,path+'/',path+'0'))
            for entry in entries:
                path=entry['path'];ref=entry['artifact']
                parent='' if path=='/' else str(PurePosixPath(path).parent)
                if not writer.query('SELECT 1 FROM workspace_files WHERE actor=? AND path=?',(self.scope.actor,path)):
                    writer.execute('INSERT INTO workspace_counts VALUES(?,?,1) ON CONFLICT(actor,parent) DO UPDATE SET total=total+1',(self.scope.actor,parent))
                if ref:writer.include_artifact(ref)
                writer.execute('INSERT INTO workspace_files VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(actor,path) DO UPDATE SET parent=excluded.parent,kind=excluded.kind,mode=excluded.mode,modified_ns=excluded.modified_ns,created_ns=excluded.created_ns,target=excluded.target,artifact=excluded.artifact,size=excluded.size',
                    (self.scope.actor,path,parent,entry['kind'],entry['mode'],entry['modified_ns'],entry['created_ns'],entry.get('target'),ref,entry['size']))
            writer.execute('INSERT INTO workspace_heads VALUES(?,?) ON CONFLICT(actor) DO UPDATE SET state=excluded.state',(self.scope.actor,state))
        self.owner.store.transaction(write)

    async def close(self):
        self._closed=True
        for task in tuple(self._tasks):task.cancel()
        await asyncio.gather(*tuple(self._tasks),return_exceptions=True)


def workspace_plugin(*,name='workspace',storage='storage',actors='actors.data'):
    def install(ctx):ctx.provide('workspace',WorkspaceStore(ctx.require(storage,'store')))
    return Plugin(name,(storage,),install,schema=WORKSPACE_SCHEMA,schema_requires=(actors,))
