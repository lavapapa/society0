"""独立只读观察：短事务、追加游标与明确的完整步骤水位。"""
from __future__ import annotations

import base64
from dataclasses import fields,is_dataclass
import json
from pathlib import Path

from .storage import StageReader
from .threads import ThreadStore, _head, _load


class ObservationError(ValueError):
    def __init__(self,code,message=None):
        self.code=code
        super().__init__(message or code)


def encoded(value):
    def default(item):
        if is_dataclass(item):return {field.name:getattr(item,field.name) for field in fields(item)}
        raise TypeError('value is not JSON serializable')
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False,default=default).encode()


def _positive(value, name, minimum=1):
    if type(value) is not int or value < minimum:
        raise ValueError('invalid ' + name)


def _same(left, right):
    return encoded(left) == encoded(right)


class _Snapshot:
    def __init__(self, view):
        self.view = view

    def read(self, callback):
        return callback(self.view)


class Observation:
    def __init__(self, path, *, information_factory=None):
        self.path = Path(path).absolute()
        self.reader = StageReader(self.path)
        self.information_factory = information_factory
        self.manifest = json.loads((self.path / 'run.json').read_text())
        if self.manifest['format'] != 2:
            raise ValueError('unsupported storage format')

    def __enter__(self):
        self.reader.__enter__()
        return self

    def close(self):
        self.reader.close()

    def __exit__(self,*exc):
        self.close()

    def _complete(self, view):
        number = view.complete_step
        last = None
        while True:
            path = self.path / 'steps' / f'{number:020d}.json'
            if not path.exists():
                if last is None:
                    raise ValueError('missing complete identity')
                return last
            value = json.loads(path.read_text())
            parent = None if number == self.manifest['root_step'] else number - 1
            if (value['run_id'] != view.run_id or type(value['step']) is not int
                    or value['step'] != number or value['parent'] != parent):
                raise ValueError('complete identity mismatch')
            last = {key: value[key] for key in ('run_id', 'step', 'live_revision')}
            number += 1

    def status(self):
        def read(view):
            failed = view.query('SELECT failed FROM _stage_runtime')[0][0]
            return {'run_id': view.run_id, 'live_revision': view.live_revision,
                    'complete_step_lower_bound': view.complete_step,
                    'complete': self._complete(view), 'failed': bool(failed)}
        result = self.reader.read(read)
        try:
            progress = json.loads((self.path / 'progress.json').read_text())
            result['progress'] = progress if progress.get('run_id') == result['run_id'] else None
        except (OSError, ValueError):
            result['progress'] = None
        return result

    @staticmethod
    def _cursor(cursor, identity, field):
        if cursor is None:
            return 0
        if not _same(cursor['identity'], identity):
            raise ObservationError('cursor_invalid','cursor identity mismatch')
        after = cursor[field]
        _positive(after, 'cursor position', 0)
        return after

    def list_threads(self, *, actor=None, cursor=None, limit=100, max_bytes=65536):
        _positive(limit, 'limit')
        _positive(max_bytes, 'page budget', 1024)
        def read(view):
            identity = [view.run_id, 'threads', actor]
            after = self._cursor(cursor, identity, 'after')
            scope = 'all' if actor is None else 'actor:' + actor
            count = view.query('SELECT total FROM thread_counts WHERE scope=?', (scope,), max_rows=1)
            total = count[0][0] if count else 0
            where, args = ('ordinal>?', (after,)) if actor is None else ('actor=? AND ordinal>?', (actor, after))
            items, used, last = [], 0, after
            def envelope(position, values):
                return {'items': values, 'total': total,
                        'cursor': {'identity': identity, 'after': position},
                        'live_revision': view.live_revision}
            for row in view.iter_query('SELECT id,actor,kind,provider_session_id,status,last_seq,message_count,ordinal FROM thread_heads WHERE ' + where + ' ORDER BY ordinal LIMIT ?', (*args, limit)):
                item = dict(zip(('id','actor','kind','provider_session_id','status','last_seq','message_count','ordinal'),row))
                ordinal = row[-1]
                length = len(encoded(item))
                if len(encoded(envelope(ordinal, []))) + used + length + len(items) > max_bytes:
                    if not items:
                        raise ValueError('page budget cannot hold Thread identity')
                    break
                items.append(item)
                used += length
                last = ordinal
            return envelope(last, items)
        return self.reader.read(read)

    def thread_tail(self, thread_id, *, cursor=None, limit=100, max_bytes=65536):
        _positive(limit, 'limit')
        _positive(max_bytes, 'page budget', 1024)
        def read(view):
            head = _head(view, thread_id)
            identity = [view.run_id, 'tail', thread_id]
            after = self._cursor(cursor, identity, 'after')
            if after > head['last_seq']:
                raise ValueError('cursor exceeds Thread')
            complete = self._complete(view)
            row = view.query('SELECT seq FROM thread_events WHERE thread_id=? AND publish_step<=? ORDER BY publish_step DESC,seq DESC LIMIT 1', (thread_id, complete['step']), max_rows=1)
            complete_through = row[0][0] if row else 0
            items, used, last = [], 0, after
            def envelope(position, values):
                return {'items': values, 'total': head['last_seq'], 'cursor': {'identity': identity, 'after': position},
                        'more': position < head['last_seq'], 'live_revision': view.live_revision,
                        'complete_step': complete['step'], 'complete_through': complete_through}
            for seq, kind, size in view.iter_query('SELECT seq,kind,raw_bytes FROM thread_events WHERE thread_id=? AND seq>? ORDER BY seq LIMIT ?', (thread_id, after, limit)):
                reference = {'run_id': view.run_id, 'thread_id': thread_id, 'seq': seq, 'total_bytes': size}
                ref_item = {'seq': seq, 'kind': kind, 'payload_ref': reference}
                item = {'seq': seq, 'kind': kind, 'payload': _load(view, thread_id, seq)} if size <= max_bytes else ref_item
                length = len(encoded(item))
                overhead = len(encoded(envelope(seq, [])))
                if overhead + used + length + len(items) > max_bytes:
                    if items:
                        break
                    item = ref_item
                    length = len(encoded(item))
                    if overhead + length > max_bytes:
                        raise ValueError('page budget cannot hold event reference')
                items.append(item)
                used += length
                last = seq
            return envelope(last, items)
        return self.reader.read(read)

    def read_thread_payload(self, reference, *, offset=0, size=65536, max_bytes=131072):
        _positive(offset, 'offset', 0)
        _positive(size, 'size')
        _positive(max_bytes, 'response budget', 1024)
        _positive(reference['seq'], 'sequence')
        def read(view):
            if reference['run_id'] != view.run_id:
                raise ValueError('reference run identity mismatch')
            # 为固定头和base64膨胀预留空间，精确边界仍在返回前验证。
            bounded = min(size, (max_bytes - 512) // 4 * 3)
            data = ThreadStore(_Snapshot(view)).read_payload(reference['thread_id'], reference['seq'], offset=offset, size=bounded)
            result = {'encoding': 'base64', 'data': base64.b64encode(data['data']).decode(),
                      'offset': offset, 'total_bytes': data['total_bytes'],
                      'next_offset': data['next_offset'] if data['next_offset'] < data['total_bytes'] else None}
            if len(encoded(result)) > max_bytes:
                raise ValueError('response budget exceeded')
            return result
        return self.reader.read(read)


    async def query(self, *, actor, moment, path, query=None, revision=None, max_bytes=65536):
        from .interaction import InteractionScope,Moment,Query
        _positive(max_bytes,'response budget',1024)
        if self.information_factory is None:
            raise ObservationError('information_factory_required')
        information=self.information_factory(self.reader)
        with InteractionScope(actor,Moment(**moment),revision) as scope:
            page=await information.query(scope,path,Query(**(query or {})))
        result={'items':page.items,'total':page.total,'next_cursor':page.next_cursor,'revision':page.revision}
        if hasattr(page,'population_total'):result['population_total']=page.population_total
        if len(encoded(result))>max_bytes:
            raise ObservationError('response_budget_exceeded','Select fewer fields or use the declared document byte reader.')
        return result

    async def read_document(self, *, actor, moment, path, revision=None, offset=0,size=65536,max_bytes=131072):
        from .interaction import InteractionScope,Moment
        _positive(offset,'offset',0)
        _positive(size,'size')
        _positive(max_bytes,'response budget',1024)
        if self.information_factory is None:
            raise ObservationError('information_factory_required')
        information=self.information_factory(self.reader)
        with InteractionScope(actor,Moment(**moment),revision) as scope:
            value=await information.read(scope,path,offset=offset,size=min(size,(max_bytes-512)//4*3))
        result={'encoding':'base64','data':base64.b64encode(value.data).decode(),'total_bytes':value.total_bytes,
                'next_offset':value.next_offset,'revision':value.revision,'source':value.source}
        if len(encoded(result))>max_bytes:raise ObservationError('response_budget_exceeded')
        return result

    def result_page(self, reference, *, cursor=None, limit=100,max_bytes=65536):
        from .results import Results
        return Results(self.reader).page(reference,cursor=cursor,limit=limit,max_bytes=max_bytes)

    def result_summary(self):
        from .results import Results
        return Results(self.reader).summary()

    def result_phases(self, *, step, cursor=None, limit=100,max_bytes=65536):
        _positive(step,'step',0)
        _positive(limit,'limit')
        _positive(max_bytes,'response budget',1024)
        def read(view):
            identity=[view.run_id,'phases',step]
            after=-1 if cursor is None else self._cursor(cursor,identity,'after')
            total=view.query('SELECT count(*) FROM result_phases WHERE step=?',(step,))[0][0]
            items=[];used=0;last=after
            def page(position,values):
                return {'items':values,'total':total,'cursor':{'identity':identity,'after':position}}
            rows=view.iter_query('SELECT p.ordinal,p.name,p.elapsed_s,p.header,s.origin FROM result_phases p JOIN result_sets s ON s.id=p.header WHERE p.step=? AND p.ordinal>? ORDER BY p.ordinal LIMIT ?',(step,after,limit))
            for ordinal,name,elapsed,header,origin in rows:
                item={'ordinal':ordinal,'name':name,'elapsed_s':elapsed,'reference':{'kind':'result_set','id':header,'origin':origin}}
                length=len(encoded(item))
                if len(encoded(page(ordinal,[])))+used+length+len(items)>max_bytes:
                    if not items:raise ObservationError('response_budget_exceeded')
                    break
                items.append(item);last=ordinal;used+=length
            return page(last,items)
        return self.reader.read(read)

    def read_result_record(self,reference,*,offset=0,size=65536,max_bytes=131072):
        from .results import Results
        _positive(size,'size')
        _positive(max_bytes,'response budget',1024)
        value=Results(self.reader).read_record(reference,offset=offset,size=min(size,(max_bytes-512)//4*3))
        result={**value,'encoding':'base64','data':base64.b64encode(value['data']).decode()}
        if len(encoded(result))>max_bytes:raise ObservationError('response_budget_exceeded')
        return result


    def resource_usage(self,*,actor=None,model=None,max_bytes=65536):
        from .usage import read
        _positive(max_bytes,'response budget',1024)
        def snapshot(view):
            result=read(view,actor=actor,model=model)
            result.update(run_id=view.run_id,live_revision=view.live_revision,complete=self._complete(view))
            return result
        result=self.reader.read(snapshot)
        if len(encoded(result))>max_bytes:
            raise ObservationError('response_too_large','select a model or increase max_bytes')
        return result

    def resource_tail(self,call_id,*,cursor=None,limit=100,max_bytes=65536):
        from ._json_chunks import decode_chunks
        _positive(limit,'limit')
        _positive(max_bytes,'page budget',1024)
        def read(view):
            rows=view.query('SELECT kind,endpoint,model,last_seq FROM resource_calls WHERE id=?',(call_id,),max_rows=1)
            if not rows:raise KeyError(call_id)
            kind,endpoint,model,total=rows[0]
            identity=[view.run_id,'resource',call_id]
            after=self._cursor(cursor,identity,'after')
            if after>total:raise ObservationError('cursor_invalid')
            items=[];used=0;last=after
            def page(position,values):
                return {'items':values,'total':total,'cursor':{'identity':identity,'after':position},
                        'kind':kind,'endpoint':endpoint,'model':model,'live_revision':view.live_revision}
            for seq,event,size in view.iter_query('SELECT seq,kind,raw_bytes FROM resource_events WHERE call_id=? AND seq>? ORDER BY seq LIMIT ?',(call_id,after,limit)):
                reference={'run_id':view.run_id,'call_id':call_id,'seq':seq,'total_bytes':size}
                ref_item={'seq':seq,'kind':event,'payload_ref':reference}
                item=ref_item
                if size<=max_bytes:
                    value=decode_chunks(body for body, in view.iter_query('SELECT payload FROM resource_chunks WHERE call_id=? AND seq=? ORDER BY chunk',(call_id,seq)))
                    item={'seq':seq,'kind':event,'payload':value}
                length=len(encoded(item));overhead=len(encoded(page(seq,[])))
                if overhead+used+length+len(items)>max_bytes:
                    if items:break
                    item=ref_item;length=len(encoded(item))
                    if overhead+length>max_bytes:raise ObservationError('response_budget_exceeded')
                items.append(item);used+=length;last=seq
            return page(last,items)
        return self.reader.read(read)

    def read_resource_payload(self,reference,*,offset=0,size=65536,max_bytes=131072):
        import zlib
        from ._json_chunks import CHUNK_BYTES
        _positive(offset,'offset',0)
        _positive(size,'size')
        _positive(reference['seq'],'sequence')
        _positive(max_bytes,'response budget',1024)
        def read(view):
            if reference['run_id']!=view.run_id:raise ObservationError('reference_invalid')
            args=(reference['call_id'],reference['seq'])
            rows=view.query('SELECT raw_bytes FROM resource_events WHERE call_id=? AND seq=?',args,max_rows=1)
            if not rows:raise KeyError(args)
            total=rows[0][0];end=min(total,offset+min(size,(max_bytes-512)//4*3));raw=bytearray()
            if offset<end:
                for start,body in view.iter_query('SELECT raw_start,payload FROM resource_chunks WHERE call_id=? AND seq=? AND chunk>=? AND chunk<=? ORDER BY chunk',(*args,offset//CHUNK_BYTES,(end-1)//CHUNK_BYTES)):
                    value=zlib.decompress(body)
                    raw.extend(value[max(0,offset-start):min(len(value),end-start)])
            result={'encoding':'base64','data':base64.b64encode(raw).decode(),'offset':offset,'total_bytes':total,'next_offset':end if end<total else None}
            if len(encoded(result))>max_bytes:raise ObservationError('response_budget_exceeded')
            return result
        return self.reader.read(read)

    def read_thread_artifact(self, *,thread_id,reference,actor,offset=0,size=65536,max_bytes=131072):
        _positive(size,'size')
        _positive(max_bytes,'response budget',1024)
        value=ThreadStore(self.reader).read_artifact(thread_id,reference,actor=actor,offset=offset,size=min(size,(max_bytes-512)//4*3))
        result={**value,'encoding':'base64','data':base64.b64encode(value['data']).decode()}
        if len(encoded(result))>max_bytes:raise ObservationError('response_budget_exceeded')
        return result


def _prepare_worker(source, destination, step, run_id, channel):
    """子进程拥有恢复写者，主观察进程持续提供 live 状态。"""
    import time
    from .storage import StageStore
    started = time.perf_counter()
    try:
        if json.loads((Path(source) / 'run.json').read_text())['run_id'] != run_id:
            raise ObservationError('source_identity_changed')
        with StageStore.prepare_readonly(source, destination, step=step,run_id=f'{run_id}:complete:{step}') as store:
            if json.loads((Path(destination) / 'run.json').read_text())['source'] != {'run_id': run_id, 'step': step}:
                raise ObservationError('source_identity_changed')
            identity = store.read(lambda view: view.run_id)
        channel.send({'state': 'ready', 'view': identity, 'step': step, 'source_run_id': run_id,
                      'elapsed_s': time.perf_counter() - started,
                      'logical_bytes': sum(item.stat().st_size for item in Path(destination).rglob('*') if item.is_file())})
    except BaseException as error:
        channel.send({'state': 'failed', 'error': type(error).__name__ + ': ' + str(error), 'step': step})
    finally:
        channel.close()


class ObservationService:
    """一个可替换的完整视图；各请求拥有自己的只读连接。"""
    def __init__(self, path, *, cache_dir=None, information_factory=None):
        import threading
        import tempfile
        self.path = Path(path).absolute()
        self.information_factory = information_factory
        self._temporary = tempfile.TemporaryDirectory(prefix='society0-observer-') if cache_dir is None else None
        self.cache_dir = Path(self._temporary.name if self._temporary else cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._pending = None
        self._ready = None
        self._attempt = {'state': 'empty'}
        self._closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _check(self):
        if self._closed:raise ObservationError('observer_closed')

    def _remove(self, path):
        import shutil
        shutil.rmtree(path, ignore_errors=True)
        for partial in self.cache_dir.glob(path.name + '.building-*'):
            shutil.rmtree(partial, ignore_errors=True)

    def _poll(self):
        if self._pending is None:
            return
        process, channel, path = self._pending
        if channel.poll():
            try:
                result = channel.recv()
            except EOFError:
                result = {'state': 'failed', 'error': 'preparation_process_exit'}
            process.join()
            channel.close()
            self._pending = None
            self._attempt = result
            if result['state'] == 'ready':
                previous = self._ready
                self._ready = (path, result)
                if previous:
                    self._remove(previous[0])
            else:
                self._remove(path)
        elif not process.is_alive():
            process.join()
            channel.close()
            self._pending = None
            self._attempt = {'state': 'failed', 'error': 'preparation_process_exit'}
            self._remove(path)

    def prepare_complete(self, step):
        import multiprocessing
        import uuid
        _positive(step, 'step', 0)
        with self._lock:
            self._check()
            self._poll()
            if self._pending:
                raise ObservationError('preparation_busy')
            with Observation(self.path) as observer:
                status = observer.status()
            if not self.path.joinpath('steps', f'{step:020d}.json').is_file() or step > status['complete']['step']:
                raise ObservationError('complete_step_unavailable')
            context = multiprocessing.get_context('spawn')
            receive, send = context.Pipe(duplex=False)
            path = self.cache_dir / ('view-' + uuid.uuid4().hex)
            process = context.Process(target=_prepare_worker, args=(str(self.path), str(path), step, status['run_id'], send), daemon=True)
            process.start()
            send.close()
            self._pending = (process, receive, path)
            self._attempt = {'state': 'preparing', 'step': step, 'source_run_id': status['run_id']}
            return dict(self._attempt)

    def preparation_status(self):
        with self._lock:
            self._check()
            self._poll()
            return dict(self._attempt)

    def clear_prepared(self):
        with self._lock:
            self._check()
            return self._clear_prepared()

    def _clear_prepared(self):
        with self._lock:
            if self._pending:
                process, channel, path = self._pending
                process.terminate()
                process.join(timeout=2)
                if process.is_alive():
                    process.kill()
                    process.join()
                channel.close()
                self._pending = None
                self._remove(path)
            if self._ready:
                self._remove(self._ready[0])
                self._ready = None
            self._attempt = {'state': 'empty'}
            return dict(self._attempt)

    def close(self):
        with self._lock:
            self._clear_prepared()
            self._closed = True
            if self._temporary:
                self._temporary.cleanup()

    def call(self, method, params=None):
        self._check()
        if params is not None and not isinstance(params,dict):
            raise ObservationError('invalid_params')
        params = dict(params or {})
        if method in ('prepare_complete', 'preparation_status', 'clear_prepared'):
            return getattr(self, method)(**params)
        if method not in ('status', 'list_threads', 'thread_tail', 'read_thread_payload','query','read_document','result_page','result_summary','result_phases','read_result_record','resource_usage','resource_tail','read_resource_payload','read_thread_artifact'):
            raise ObservationError('unknown_method')
        view = params.pop('view', None)
        def read(path):
            with Observation(path, information_factory=self.information_factory) as observer:
                value=getattr(observer,method)(**params)
                from inspect import isawaitable
                if isawaitable(value):
                    import asyncio
                    return asyncio.run(value)
                return value
        if view is None:
            return read(self.path)
        with self._lock:
            self._check()
            self._poll()
            if self._ready is None or self._ready[1]['view'] != view:
                raise ObservationError('view_expired')
            return read(self._ready[0])


def make_server(service, *, host='127.0.0.1', port=0, capacity=4, timeout=5,
                max_request_bytes=1048576, max_response_bytes=16777216):
    """标准库有界工作槽；慢客户端有读写超时，服务不持有运行写者。"""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from socketserver import ThreadingMixIn
    import apsw
    from .storage import StorageError
    _positive(capacity, 'capacity')
    _positive(max_request_bytes,'request budget')
    _positive(max_response_bytes,'response budget',1024)
    if timeout <= 0:
        raise ValueError('invalid socket timeout')

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(timeout)

        def do_POST(self):
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= max_request_bytes:
                    raise ValueError('invalid_request_size')
                request = json.loads(self.rfile.read(length))
                if not isinstance(request, dict) or not isinstance(request.get('method'), str):
                    raise ValueError('invalid_request')
                params = request.get('params', {})
                if not isinstance(params, dict):
                    raise ValueError('invalid_params')
                if 'max_bytes' in params and (type(params['max_bytes']) is not int or params['max_bytes']>max_response_bytes):
                    raise ObservationError('response_budget_exceeded')
                payload = encoded(service.call(request['method'], params))
                if len(payload) > max_response_bytes:
                    raise ObservationError('response_budget_exceeded')
                code = 200
            except (ValueError, TypeError, KeyError, LookupError, OSError, StorageError, apsw.Error) as error:
                payload = encoded({'error': {'code': getattr(error,'code',type(error).__name__), 'message': str(error)}})
                code = 400
            try:
                self.send_response(code)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except OSError:
                pass

        def log_message(self, *args):
            pass

    class Server(ThreadingMixIn, HTTPServer):
        daemon_threads = False
        block_on_close = True

        def __init__(self):
            self._slots = threading.BoundedSemaphore(capacity)
            self._counter_lock = threading.Lock()
            self.active_requests = 0
            super().__init__((host, port), Handler)

        def process_request(self, request, address):
            if not self._slots.acquire(blocking=False):
                try:
                    request.settimeout(.1)
                    payload = b'{"error":{"code":"server_busy"}}'
                    request.sendall(b'HTTP/1.0 503 Service Unavailable\r\nContent-Type: application/json\r\nContent-Length: '
                                    + str(len(payload)).encode() + b'\r\n\r\n' + payload)
                except OSError:
                    pass
                finally:
                    self.shutdown_request(request)
                return
            with self._counter_lock:
                self.active_requests += 1
            try:
                super().process_request(request, address)
            except BaseException:
                self._release()
                raise

        def _release(self):
            with self._counter_lock:
                self.active_requests -= 1
            self._slots.release()

        def process_request_thread(self, request, address):
            try:
                super().process_request_thread(request, address)
            finally:
                self._release()

    return Server()


def main(argv=None):
    import argparse
    import time
    import apsw
    from .storage import StorageError
    parser = argparse.ArgumentParser(description='Read a Society0 run without starting its plugins or models.')
    parser.add_argument('run')
    parser.add_argument('--request', help='JSON object with method and params')
    parser.add_argument('--serve', type=int)
    parser.add_argument('--capacity', type=int, default=4)
    parser.add_argument('--timeout', type=float, default=5)
    parser.add_argument('--cache-dir')
    parser.add_argument('--complete-step',type=int,help='Prepare this complete step, execute one read, then remove the temporary view')
    parser.add_argument('--information-factory',help='Explicit readonly module:callable factory')
    args = parser.parse_args(argv)
    try:
        factory=None
        if args.information_factory:
            from importlib import import_module
            module,name=args.information_factory.split(':',1)
            factory=getattr(import_module(module),name)
        with ObservationService(args.run, cache_dir=args.cache_dir,information_factory=factory) as service:
            if args.serve is not None:
                if args.complete_step is not None:
                    raise ObservationError('invalid_params','Use prepare_complete requests with --serve.')
                server = make_server(service, port=args.serve, capacity=args.capacity, timeout=args.timeout)
                try:
                    server.serve_forever()
                except KeyboardInterrupt:
                    pass
                finally:
                    server.server_close()
            else:
                request = json.loads(args.request) if args.request else {'method': 'status'}
                if not isinstance(request,dict):raise ObservationError('invalid_request')
                if request['method'] in ('prepare_complete','preparation_status','clear_prepared'):
                    raise ObservationError('session_required','Use --serve for preparation lifecycle, or --complete-step N for one historical read.')
                params=request.get('params',{})
                if not isinstance(params,dict):raise ObservationError('invalid_params')
                if args.complete_step is not None:
                    service.prepare_complete(args.complete_step)
                    while True:
                        state=service.preparation_status()
                        if state['state']!='preparing':break
                        time.sleep(.01)
                    if state['state']!='ready':raise ObservationError('preparation_failed',state.get('error'))
                    params={**params,'view':state['view']}
                print(encoded(service.call(request['method'],params)).decode())
        return 0
    except (ValueError,TypeError,LookupError,OSError,StorageError,apsw.Error) as error:
        print(encoded({'error':{'code':getattr(error,'code',type(error).__name__),'message':str(error)}}).decode())
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
