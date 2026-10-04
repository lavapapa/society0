"""主体绑定的原文文件视图；共享信息、认知、私有文件与结果共用路径。"""
from __future__ import annotations

import base64
import codecs
from dataclasses import asdict, is_dataclass
import fnmatch
import io
import json
from pathlib import PurePosixPath
import tempfile
from urllib.parse import quote, unquote
import uuid

from .interaction import Page, ResourceStat, Ref, Unavailable
from ..async_utils import invoke_maybe_async


def _json(value):
    return json.dumps(value,ensure_ascii=False,default=lambda item:asdict(item) if is_dataclass(item) else str(item))


def text_range(data, offset, total, encoding):
    if encoding == 'base64':
        text,consumed=base64.b64encode(data).decode('ascii'),len(data)
    elif encoding == 'utf-8':
        if len(data)<4 and offset+len(data)<total:
            raise ValueError('UTF-8 byte budget must be at least four')
        decoder=codecs.getincrementaldecoder('utf-8')()
        try:text=decoder.decode(data,final=offset+len(data)==total)
        except UnicodeDecodeError as error:raise ValueError('binary content requires encoding=base64') from error
        consumed=len(data)-len(decoder.getstate()[0])
    else:raise ValueError('encoding must be utf-8 or base64')
    end=offset+consumed
    return {'data':text,'encoding':encoding,'total_bytes':total,'next_offset':end if end<total else None}


class ActorFiles:
    def __init__(self, session, threads, *, shell=None):
        self.session,self.threads,self.shell=session,threads,shell
        self._context={}
        self._world_projection=None
        self._closed=False

    def _check(self):
        if self._closed:raise RuntimeError('actor files are closed')
        self.session.scope.check_active()

    @staticmethod
    def result_path(reference):
        return '/results/'+quote(reference,safe='')

    @staticmethod
    def _path(path):
        if not isinstance(path,str) or not path.startswith('/') or '..' in PurePosixPath(path).parts:
            raise ValueError('file path must be absolute without parent traversal')
        return str(PurePosixPath(path))

    def _activation(self):
        return self.session.cursors.get('activation')

    def _mount(self,path):
        relative=path[len('/context/'):]
        name,_,tail=relative.partition('/')
        context=self._activation()
        mount=getattr(context,'mounts',{}).get(name)
        if mount is None:raise FileNotFoundError(path)
        return mount,'/'+tail

    def _context_file(self, path):
        if path not in ('/context/messages.json','/context/experience.json'):
            raise FileNotFoundError(path)
        if path not in self._context:
            context=self._activation()
            if context is None:raise FileNotFoundError(path)
            if path.endswith('messages.json'):
                inputs=getattr(context,'inputs',None)
                initial=getattr(inputs,'messages',inputs) or ()
                background=getattr(inputs,'context',None)
                if background is None and getattr(inputs,'consumer',None) is not None and context.thread_id is not None:
                    background=self.threads.input_context(context.thread_id,inputs.consumer)
                value=([background] if background is not None else [])+[*initial,*context.messages]
            else:value=context.experience
            stream=tempfile.TemporaryFile()
            writer=io.TextIOWrapper(stream,encoding='utf-8')
            json.dump(value,writer,ensure_ascii=False,default=lambda item:asdict(item) if is_dataclass(item) else str(item))
            writer.flush();stream=writer.detach();total=stream.tell();stream.seek(0)
            self._context[path]=(stream,total)
        return self._context[path]

    def _projection(self):
        if self._world_projection is None:
            from .information_fs import InformationFiles
            self._world_projection=InformationFiles(self.session.information,self.session.scope)
        return self._world_projection

    @staticmethod
    def _transport(path):
        return any(part.startswith('@') for part in PurePosixPath(path).parts)

    def _workspace(self):
        if self.shell is None or not self.shell.has_workspace:raise FileNotFoundError('/workspace')
        return self.shell

    async def ls(self,path='/',*,limit=100,cursor=None):
        self._check();path=self._path(path)
        if type(limit) is not int or limit<1:raise ValueError('limit must be positive')
        if path=='/':items=[{'path':'/'+name,'kind':'directory'} for name in ('world','context','workspace','results')]
        elif path=='/world' or path.startswith('/world/'):
            source=path[6:] or '/'
            if self._transport(source):
                entries=await self._projection().callback('list',source)
                offset=0 if cursor is None else cursor['offset']
                end=min(offset+limit,len(entries))
                revision=await self.revision('/world'+self._projection()._parse(source)[0])
                if cursor is not None and cursor['revision']!=revision:raise ValueError('transport directory changed')
                return Page([{'path':path.rstrip('/')+'/'+name,'kind':kind,'total_bytes':size} for name,kind,size,*rest in entries[offset:end]],
                    len(entries),{'offset':end,'revision':revision} if end<len(entries) else None,revision)
            try:metadata=await self.session.information.metadata(source)
            except Unavailable:metadata=None
            if metadata is None:
                page=await self.session.information.list_files(source,limit=limit,cursor=cursor)
                return Page([{**item,'path':'/world'+item['path']} for item in page.items],page.total,page.next_cursor,page.revision)
            first=cursor is None
            if cursor is not None and cursor['revision']!=metadata['revision']:raise ValueError('schema directory changed')
            provider_cursor=None if first else cursor['provider']
            page=await self.session.information.list_files(source,limit=max(1,limit-1) if first else limit,cursor=provider_cursor)
            items=[{**item,'path':'/world'+item['path']} for item in page.items]
            if first:
                schema={'path':path.rstrip('/')+'/@schema.json','kind':'file','revision':metadata['revision']}
                if limit==1:
                    return Page([schema],page.total+1,{'provider':None,'revision':metadata['revision']} if page.total else None,metadata['revision'])
                items.insert(0,schema)
            next_cursor=None if page.next_cursor is None else {'provider':page.next_cursor,'revision':metadata['revision']}
            return Page(items,page.total+1,next_cursor,metadata['revision'])
        elif path=='/results':
            page=self.threads.list_artifacts(actor=self.session.actor.id,limit=limit,cursor=cursor)
            return Page([{'path':self.result_path(item['reference']),'kind':'file','revision':item['artifact']} for item in page.items],page.total,page.next_cursor,page.revision)
        elif path=='/context':
            items=[{'path':'/context/messages.json','kind':'file'},{'path':'/context/experience.json','kind':'file'}]
            items.extend({'path':'/context/'+name,'kind':'directory'} for name in getattr(self._activation(),'mounts',{}))
        elif path.startswith('/context/'):
            mount,relative=self._mount(path)
            page=await invoke_maybe_async(mount.list,relative,limit=limit,cursor=cursor)
            prefix='/context/'+path[len('/context/'):].partition('/')[0]
            return Page([{**item,'path':prefix+item['path']} for item in page.items],page.total,page.next_cursor,page.revision)
        elif path=='/workspace' or path.startswith('/workspace/'):
            return await self._workspace().workspace_list(path[10:] or '/',limit=limit,cursor=cursor)
        else:raise NotADirectoryError(path)
        revision=[self.session.actor.id,self.session.step,self.session.moment.time]
        offset=0 if cursor is None else cursor['offset']
        if cursor is not None and cursor['revision']!=revision:raise ValueError('directory changed')
        end=min(offset+limit,len(items))
        return Page(items[offset:end],len(items),{'offset':end,'revision':revision} if end<len(items) else None,revision)

    async def _raw(self,path,*,offset=0,size=65536,expected_revision=None):
        self._check();path=self._path(path)
        if type(offset) is not int or offset<0 or type(size) is not int or size<1:raise ValueError('invalid byte range')
        if path.startswith('/world/') and path.endswith('/@schema.json'):
            metadata=await self.session.information.metadata(path[6:-13])
            raw=_json(metadata).encode();revision=metadata['revision']
            if expected_revision is not None and _json(expected_revision)!=_json(revision):raise ValueError('schema revision changed')
            return raw[offset:offset+size],len(raw),revision,path
        if path.startswith('/world/') and self._transport(path[6:]):
            projection=self._projection();source=path[6:]
            original=projection._parse(source)[0]
            revision=await self.session.information.search_revision(original)
            if expected_revision is not None and _json(expected_revision)!=_json(revision):raise ValueError('transport revision changed')
            raw=await projection.callback('read',source)
            if _json(await self.session.information.search_revision(original))!=_json(revision):raise ValueError('transport changed during read')
            return raw[offset:offset+size],len(raw),revision,path
        if path=='/world' or path.startswith('/world/'):
            chunk=await self.session.information.read(path[6:] or '/',offset=offset,size=size,expected_revision=expected_revision)
            return chunk.data,chunk.total_bytes,chunk.revision,chunk.source
        if path.startswith('/results/'):
            item=self.threads.read_actor_artifact(unquote(path[9:]),actor=self.session.actor.id,offset=offset,size=size)
            value=(item['data'],item['total_bytes'],item['source'],item['source'])
        elif path in ('/context/messages.json','/context/experience.json'):
            stream,total=self._context_file(path);stream.seek(offset)
            value=(stream.read(size),total,[self.session.actor.id,self.session.step],path)
        elif path.startswith('/context/'):
            mount,relative=self._mount(path)
            chunk=await invoke_maybe_async(mount.read,relative,offset=offset,size=size,expected_revision=expected_revision)
            return chunk.data,chunk.total_bytes,chunk.revision,chunk.source
        elif path=='/workspace' or path.startswith('/workspace/'):
            return await self._workspace().workspace_read(path[10:] or '/',offset=offset,size=size,expected_revision=expected_revision)
        else:raise FileNotFoundError(path)
        if expected_revision is not None and _json(value[2])!=_json(expected_revision):raise ValueError('file revision changed')
        self._check();return value

    async def read(self,path,*,offset=0,size=65536,encoding='utf-8',expected_revision=None):
        if encoding=='utf-8' and size<4:raise ValueError('UTF-8 byte budget must be at least four')
        data,total,revision,source=await self._raw(path,offset=offset,size=size,expected_revision=expected_revision)
        return {**text_range(data,offset,total,encoding),'revision':revision,'source':source,'path':path}

    async def stat(self,path):
        self._check();path=self._path(path)
        if (path=='/world' or path.startswith('/world/')) and not self._transport(path[6:]):
            return await self.session.information.stat(path[6:] or '/')
        try:
            data,total,revision,source=await self._raw(path,size=1)
            return ResourceStat('file',total,revision,source)
        except (IsADirectoryError,NotADirectoryError,FileNotFoundError):
            page=await self.ls(path,limit=1)
            return ResourceStat('directory',None,page.revision,Ref('files','directory',path))

    async def revision(self,path):
        path=self._path(path)
        if path=='/':return [await self.revision('/'+name) for name in ('world','context','workspace','results')]
        if path=='/world' or path.startswith('/world/'):
            return await self.session.information.search_revision(path[6:] or '/')
        if path=='/workspace' or path.startswith('/workspace/'):
            if self.shell is None or not self.shell.has_workspace:return None
            return self.shell.workspace_revision()
        if path=='/results' or path.startswith('/results/'):
            return self.threads.list_artifacts(actor=self.session.actor.id,limit=1).revision
        context=self._activation()
        mounts=getattr(context,'mounts',{})
        if path.startswith('/context/') and path not in ('/context/messages.json','/context/experience.json'):
            mount,_=self._mount(path)
            return await invoke_maybe_async(mount.revision)
        return [self.session.actor.id,self.session.step,
                {name:await invoke_maybe_async(mount.revision) for name,mount in mounts.items()}]

    async def _walk(self,path):
        stat=await self.stat(path)
        if stat.kind=='file':yield path;return
        cursor=None
        while True:
            page=await self.ls(path,limit=100,cursor=cursor)
            for item in page.items:
                if item['path'].endswith('/@schema.json'):continue
                if item['kind']=='directory':
                    # /workspace 始终可发现，持久插件由研究者明确选择。
                    if item['path']=='/workspace' and (self.shell is None or not self.shell.has_workspace):continue
                    async for child in self._walk(item['path']):yield child
                else:yield item['path']
            cursor=page.next_cursor
            if cursor is None:break

    def _register(self,stream,kind):
        stream.flush();stream.seek(0)
        artifact=self.session.prepare_artifact(iter(lambda:stream.read(65536),b''))
        reference=kind+'-'+uuid.uuid4().hex+'.jsonl'
        self.threads.register_artifact(self.session.cursors['thread_id'],reference,artifact,actor=self.session.actor.id)
        return self.result_path(reference)

    async def find(self,pattern,path='/',*,limit=100,cursor=None):
        self._check()
        if self._transport(path):raise ValueError('find selects original file paths, excluding transmission metadata')
        if type(limit) is not int or limit<1:raise ValueError('limit must be positive')
        if cursor is not None:
            if cursor['pattern']!=pattern or cursor['path']!=path:raise ValueError('find cursor mismatch')
            result_path=cursor['result_path'];offset=cursor['offset'];total=cursor['total'];revision=cursor['revision']
        else:
            revision=await self.revision(path);total=0
            with tempfile.TemporaryFile() as stream:
                async for candidate in self._walk(path):
                    relative=str(PurePosixPath(candidate).relative_to(path)) if candidate!=path else PurePosixPath(candidate).name
                    if fnmatch.fnmatchcase(relative,pattern) or fnmatch.fnmatchcase(candidate,pattern):
                        stream.write((_json({'path':candidate,'kind':'file'})+'\n').encode());total+=1
                if _json(await self.revision(path))!=_json(revision):raise ValueError('selected files changed during find')
                result_path=self._register(stream,'find');offset=0
                revision=await self.revision(path)
        if _json(await self.revision(path))!=_json(revision):raise ValueError('selected files changed during find')
        # JSONL 行是结果记录，正文独立留在原文件。
        artifact=self.threads.lookup_actor_artifact(unquote(result_path[9:]),actor=self.session.actor.id)
        store=self.threads.store
        _,length=store.read_artifact(artifact,size=1)
        scope=self.session.scope
        class ArtifactReader(io.RawIOBase):
            def __init__(self):self.offset=offset
            def readable(self):return True
            def tell(self):return self.offset
            def readinto(self,buffer):
                scope.check_active()
                data,_=store.read_artifact(artifact,offset=self.offset,size=len(buffer))
                buffer[:len(data)]=data;self.offset+=len(data)
                return len(data)
        items=[]
        with io.BufferedReader(ArtifactReader(),buffer_size=65536) as reader:
            for _ in range(limit):
                line=reader.readline()
                if not line:break
                items.append(json.loads(line))
            offset=reader.tell()
        continuation={'pattern':pattern,'path':path,'result_path':result_path,'offset':offset,'total':total,'revision':revision} if offset<length else None
        return Page(items,total,continuation,revision)

    async def grep(self,pattern,path='/',*,glob=None,literal=False,ignore_case=False):
        self._check()
        if self._transport(path):raise ValueError('grep selects original file paths, excluding transmission metadata')
        try:
            from society0_filesystem import search_reader as native_search
        except ImportError as error:
            raise ValueError('grep requires society0-filesystem; install society0[llm]') from error
        from .information_fs import search_reader
        revision=await self.revision(path);count=0;bytes_read=0;sink_batches=0;preview=[]
        with tempfile.TemporaryFile() as stream:
            async for candidate in self._walk(path):
                if glob is not None and not fnmatch.fnmatchcase(candidate,glob) and not fnmatch.fnmatchcase(PurePosixPath(candidate).name,glob):continue
                stat=await self.stat(candidate)
                decoder=codecs.getincrementaldecoder('utf-8')()
                async def read(offset,size):
                    data,total,current,_=await self._raw(candidate,offset=offset,size=size,expected_revision=stat.revision)
                    if b'\x00' in data:raise ValueError('grep selects text files; use read encoding=base64 for binary content')
                    try:decoder.decode(data,final=offset+len(data)>=total)
                    except UnicodeDecodeError as error:raise ValueError('grep selects UTF-8 text files; use read encoding=base64 for binary content') from error
                    return data
                async def sink(line_number,byte_offset,line):
                    nonlocal count
                    item={'path':candidate,'revision':stat.revision,'line_number':line_number,'byte_offset':byte_offset,
                          'line':line.decode('utf-8')}
                    stream.write((_json(item)+'\n').encode());count+=1
                    if len(preview)<20:preview.append({**item,'line':item['line'][:1000]})
                try:result=await search_reader(read,sink,pattern,literal=literal,ignore_case=ignore_case)
                except RuntimeError as error:raise ValueError('grep failed: '+str(error)) from error
                bytes_read+=result['bytes_read'];sink_batches+=result['sink_batches']
            if _json(await self.revision(path))!=_json(revision):raise ValueError('selected files changed during grep')
            result_path=self._register(stream,'grep')
        return {'status':'completed','total':count,'items':preview,'result_path':result_path,'next_offset':0,
                'revision':revision,'bytes_read':bytes_read,'sink_batches':sink_batches}

    async def callback(self,operation,path):
        if operation=='list':
            result=[];cursor=None
            while True:
                page=await self.ls(path,cursor=cursor)
                result.extend((PurePosixPath(item['path']).name,item['kind'],0,0o555 if item['kind']=='directory' else 0o444,0,0) for item in page.items)
                cursor=page.next_cursor
                if cursor is None:return result
        if operation=='stat':
            stat=await self.stat(path);return (stat.kind,stat.total_bytes or 0,0o444 if stat.kind=='file' else 0o555,0,0)
        if operation=='exists':
            try:await self.stat(path);return True
            except (FileNotFoundError,KeyError,Unavailable):return False
        if operation=='read':
            pieces=[];offset=0;revision=None
            while True:
                data,total,revision,_=await self._raw(path,offset=offset,expected_revision=revision)
                if not data and offset<total:raise ValueError('file returned an incomplete byte range')
                pieces.append(data);offset+=len(data)
                if offset>=total:return b''.join(pieces)
        raise ValueError('read-only files do not contain symlinks')

    async def close(self):
        self._closed=True
        for stream,_ in self._context.values():stream.close()
        if self._world_projection is not None:await self._world_projection.close()
