"""Thread 权威消息、请求引用和独立压缩正文块。"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
import json
import uuid
import zlib

from ._json_chunks import CHUNK_BYTES, decode_chunks
THREAD_SCHEMA = (
    "CREATE TABLE thread_input_cursors(thread_id TEXT NOT NULL,consumer TEXT NOT NULL,event_seq INTEGER NOT NULL,context_seq INTEGER,PRIMARY KEY(thread_id,consumer))",
    '''CREATE TABLE thread_heads(
        id TEXT PRIMARY KEY NOT NULL,actor TEXT NOT NULL,kind TEXT NOT NULL,
        provider_session_id TEXT NOT NULL,status TEXT NOT NULL,
        last_seq INTEGER NOT NULL,message_count INTEGER NOT NULL,ordinal INTEGER NOT NULL,moment TEXT NOT NULL)''',
    'CREATE INDEX thread_moment ON thread_heads(actor,moment,kind,ordinal)',
    '''CREATE TABLE thread_artifacts(thread_id TEXT NOT NULL,reference TEXT NOT NULL,artifact TEXT NOT NULL,PRIMARY KEY(thread_id,reference))''',
    '''CREATE TABLE thread_tool_receipts(thread_id TEXT NOT NULL,call_id TEXT NOT NULL,event_seq INTEGER NOT NULL,message_seq INTEGER NOT NULL,PRIMARY KEY(thread_id,call_id))''',
    'CREATE UNIQUE INDEX thread_order ON thread_heads(ordinal)',
    'CREATE INDEX thread_actor_order ON thread_heads(actor,ordinal)',
    '''CREATE TABLE thread_counts(scope TEXT PRIMARY KEY NOT NULL,total INTEGER NOT NULL)''',
    '''CREATE TABLE thread_events(thread_id TEXT NOT NULL,seq INTEGER NOT NULL,kind TEXT NOT NULL,raw_bytes INTEGER NOT NULL,publish_step INTEGER NOT NULL,
        PRIMARY KEY(thread_id,seq))''',
    'CREATE INDEX thread_published ON thread_events(thread_id,publish_step,seq)',
    'CREATE INDEX thread_messages ON thread_events(thread_id,seq) WHERE kind=\'message\'',
    '''CREATE TABLE thread_chunks(thread_id TEXT NOT NULL,seq INTEGER NOT NULL,chunk INTEGER NOT NULL,
        raw_bytes INTEGER NOT NULL,payload BLOB NOT NULL,PRIMARY KEY(thread_id,seq,chunk))''',
)


def _load(view, thread_id, sequence):
    rows = view.iter_query('SELECT payload FROM thread_chunks WHERE thread_id=? AND seq=? ORDER BY chunk', (thread_id,sequence))
    return decode_chunks(payload for (payload,) in rows)


def _head(view, thread_id):
    rows = view.query('SELECT actor,kind,provider_session_id,status,last_seq,message_count,ordinal FROM thread_heads WHERE id=?', (thread_id,))
    if not rows:
        raise KeyError(thread_id)
    return dict(zip(('actor','kind','provider_session_id','status','last_seq','message_count','ordinal'), rows[0]), id=thread_id)


def _append(writer, thread_id, kind, payload, *, allow_closed=False):
    head = _head(writer, thread_id)
    if head['status'] != 'open' and not allow_closed:
        raise ValueError('Thread is closed')
    seq = head['last_seq'] + 1
    writer.execute('INSERT INTO thread_events VALUES(?,?,?,0,?)', (thread_id, seq, kind, writer.publish_step))
    total = 0
    def rows():
        nonlocal total
        for index, (size, body) in enumerate(writer.encode_chunks(payload)):
            total += size
            yield thread_id, seq, index, size, body
    writer.executemany('INSERT INTO thread_chunks VALUES(?,?,?,?,?)', rows())
    writer.execute('UPDATE thread_events SET raw_bytes=? WHERE thread_id=? AND seq=?', (total,thread_id,seq))
    writer.execute('UPDATE thread_heads SET last_seq=?,message_count=message_count+? WHERE id=?', (seq, int(kind == 'message'), thread_id))
    return seq


def _messages(view, thread_id, *, after_seq=0, max_messages=None, through=None):
    output = []
    through = _head(view, thread_id)['last_seq'] if through is None else through
    while max_messages is None or len(output) < max_messages:
        count = 128 if max_messages is None else min(128, max_messages - len(output))
        rows = view.query("SELECT seq FROM thread_events WHERE thread_id=? AND kind='message' AND seq>? AND seq<=? ORDER BY seq LIMIT ?",
                          (thread_id, after_seq, through, count), max_rows=count)
        if not rows:
            break
        for (after_seq,) in rows:
            output.append(_load(view, thread_id, after_seq))
    return output


class ThreadStore:
    def __init__(self, store):
        self.store = store

    def open(self, actor, moment, kind, metadata=None, *, provider_session_id=None):
        thread_id = uuid.uuid4().hex
        provider_session_id = provider_session_id or uuid.uuid4().hex
        if is_dataclass(moment):
            moment = asdict(moment)
        def write(writer):
            for scope in ('all', 'actor:' + actor):
                writer.execute('INSERT INTO thread_counts VALUES(?,1) ON CONFLICT(scope) DO UPDATE SET total=total+1', (scope,))
            ordinal = writer.query("SELECT total FROM thread_counts WHERE scope='all'")[0][0]
            writer.execute('INSERT INTO thread_heads VALUES(?,?,?,?,?,0,0,?,?)', (thread_id,actor,kind,provider_session_id,'open',ordinal,json.dumps(moment,sort_keys=True,allow_nan=False)))
            _append(writer, thread_id, 'opened', {'moment': moment, 'metadata': metadata})
        self.store.transaction(write)
        return thread_id

    def find(self, actor, moment, kind='decision'):
        if is_dataclass(moment):
            moment = asdict(moment)
        key = json.dumps(moment,sort_keys=True,allow_nan=False)
        rows = self.store.read(lambda view:view.query('SELECT id FROM thread_heads WHERE actor=? AND moment=? AND kind=? ORDER BY ordinal DESC LIMIT 1', (actor,key,kind)))
        return rows[0][0] if rows else None

    def save_tool_result(self, thread_id, call, content, *, metadata=None):
        def write(writer):
            rows = writer.query('SELECT event_seq,message_seq FROM thread_tool_receipts WHERE thread_id=? AND call_id=?', (thread_id,call['id']))
            if rows:
                previous = _load(writer,thread_id,rows[0][0])
                previous_call = previous['call']
                previous_content = _load(writer,thread_id,rows[0][1])['content']
                if previous_call != call or previous_content != content or previous['metadata'] != metadata:
                    raise ValueError('tool call identity reused with different content')
                return rows[0][1]
            event = _append(writer,thread_id,'tool_receipt',{'call':call,'metadata':metadata})
            message = _append(writer,thread_id,'message',{'role':'tool','tool_call_id':call['id'],'content':content})
            writer.execute('INSERT INTO thread_tool_receipts VALUES(?,?,?,?)', (thread_id,call['id'],event,message))
            return message
        return self.store.transaction(write)

    def get_tool_result(self, thread_id, call_id):
        def read(view):
            _head(view,thread_id)
            rows = view.query('SELECT event_seq,message_seq FROM thread_tool_receipts WHERE thread_id=? AND call_id=?', (thread_id,call_id))
            if not rows:
                return None
            payload = _load(view,thread_id,rows[0][0])
            return {'call':payload['call'],'metadata':payload['metadata'], 'content':_load(view,thread_id,rows[0][1])['content']}
        return self.store.read(read)

    def register_artifact(self, thread_id, reference, artifact_ref, *, actor):
        def write(writer):
            if _head(writer,thread_id)['actor'] != actor:
                raise PermissionError('Thread owner differs')
            rows = writer.query('SELECT artifact FROM thread_artifacts WHERE thread_id=? AND reference=?',(thread_id,reference))
            if rows:
                if rows[0][0] != artifact_ref:
                    raise ValueError('artifact reference already registered')
                return
            writer.include_artifact(artifact_ref)
            writer.execute('INSERT INTO thread_artifacts VALUES(?,?,?)',(thread_id,reference,artifact_ref))
        return self.store.transaction(write)

    def lookup_artifact(self, thread_id, reference, *, actor):
        def read(view):
            if _head(view,thread_id)['actor'] != actor:
                raise PermissionError('Thread owner differs')
            rows = view.query('SELECT artifact FROM thread_artifacts WHERE thread_id=? AND reference=?',(thread_id,reference))
            if not rows:
                raise KeyError(reference)
            return rows[0][0]
        return self.store.read(read)

    def read_artifact(self, thread_id, reference, *, actor, offset=0, size=65536):
        artifact = self.lookup_artifact(thread_id,reference,actor=actor)
        data,total = self.store.read_artifact(artifact,offset=offset,size=size)
        end = offset+len(data)
        return {'data':data,'total_bytes':total,'next_offset':end if end<total else None,'source':artifact}

    def append_input(self, thread_id, messages, consumer, cursor, *, context=None):
        def write(writer):
            previous=writer.query('SELECT context_seq FROM thread_input_cursors WHERE thread_id=? AND consumer=?',(thread_id,consumer))
            context_seq=previous[0][0] if previous else None
            if context is not None:
                if type(context) is not dict:raise TypeError('context must be a JSON object')
                context_seq=_append(writer,thread_id,'message',context)
            for message in messages:
                if type(message) is not dict:
                    raise TypeError('message must be a JSON object')
                _append(writer,thread_id,'message',message)
            sequence=_append(writer,thread_id,'input_cursor',{'consumer':consumer,'cursor':cursor})
            writer.execute('INSERT INTO thread_input_cursors VALUES(?,?,?,?) ON CONFLICT(thread_id,consumer) DO UPDATE SET event_seq=excluded.event_seq,context_seq=excluded.context_seq',
                           (thread_id,consumer,sequence,context_seq))
            return sequence
        return self.store.transaction(write)

    def input_context(self, thread_id, consumer):
        def read(view):
            _head(view,thread_id)
            rows=view.query('SELECT context_seq FROM thread_input_cursors WHERE thread_id=? AND consumer=?',(thread_id,consumer))
            return _load(view,thread_id,rows[0][0]) if rows and rows[0][0] is not None else None
        return self.store.read(read)

    def input_cursor(self, thread_id, consumer):
        def read(view):
            _head(view,thread_id)
            rows=view.query('SELECT event_seq FROM thread_input_cursors WHERE thread_id=? AND consumer=?',(thread_id,consumer))
            return _load(view,thread_id,rows[0][0])['cursor'] if rows else None
        return self.store.read(read)

    def append_message(self, thread_id, message):
        if type(message) is not dict:
            raise TypeError('message must be a JSON object')
        return self.store.transaction(lambda writer:_append(writer, thread_id, 'message', message))

    def event(self, thread_id, kind, payload):
        if kind in ('message','opened','closed','reopened','request','tool_receipt','input_cursor'):
            raise ValueError('reserved Thread event kind')
        return self.store.transaction(lambda writer:_append(writer, thread_id, kind, payload))

    def record_request(self, thread_id, *, provider_options, physical_request_id, message_seqs=None, retry_of=None, through=None):
        def write(writer):
            head = _head(writer, thread_id)
            watermark = head['last_seq'] if through is None else through
            if type(watermark) is not int or not 0 <= watermark <= head['last_seq']:
                raise ValueError('invalid request message watermark')
            if message_seqs is not None:
                for sequence in message_seqs:
                    if type(sequence) is not int or sequence > watermark or not writer.query("SELECT 1 FROM thread_events WHERE thread_id=? AND seq=? AND kind='message'", (thread_id,sequence)):
                        raise ValueError('request references a missing message')
            payload = {'through': watermark, 'message_seqs': message_seqs, 'provider_options': provider_options,
                       'physical_request_id': physical_request_id, 'retry_of': retry_of,
                       'provider_session_id': head['provider_session_id']}
            return _append(writer, thread_id, 'request', payload)
        return self.store.transaction(write)

    def read_request(self, thread_id, seq):
        def read(view):
            if not view.query("SELECT 1 FROM thread_events WHERE thread_id=? AND seq=? AND kind='request'", (thread_id,seq)):
                raise KeyError(seq)
            payload = _load(view, thread_id, seq)
            refs = payload.pop('message_seqs')
            through = payload.pop('through')
            payload['messages'] = (_messages(view, thread_id, through=through) if refs is None else
                                   [_load(view, thread_id, item) for item in refs])
            return payload
        return self.store.read(read)

    def snapshot_messages(self, thread_id, *, through=None):
        def read(view):
            latest = _head(view,thread_id)['last_seq']
            watermark = latest if through is None else through
            if type(watermark) is not int or not 0 <= watermark <= latest:
                raise ValueError('invalid message watermark')
            return {'messages':_messages(view,thread_id,through=watermark),'through':watermark}
        return self.store.read(read)

    def read_messages(self, thread_id, *, after_seq=0, max_messages=None):
        if max_messages is not None and (type(max_messages) is not int or max_messages < 0):
            raise ValueError('invalid message limit')
        return self.store.read(lambda view:_messages(view, thread_id, after_seq=after_seq, max_messages=max_messages))

    def describe(self, thread_id):
        return self.store.read(lambda view:_head(view, thread_id))

    def close(self, thread_id, outcome):
        if outcome not in ('completed','waiting','incomplete'):
            raise ValueError('unknown Thread outcome')
        def write(writer):
            seq = _append(writer, thread_id, 'closed', {'outcome': outcome})
            writer.execute('UPDATE thread_heads SET status=? WHERE id=?', (outcome,thread_id))
            return seq
        return self.store.transaction(write)

    def reopen(self, thread_id):
        def write(writer):
            if _head(writer, thread_id)['status'] == 'open':
                return
            _append(writer, thread_id, 'reopened', {}, allow_closed=True)
            writer.execute("UPDATE thread_heads SET status='open' WHERE id=?", (thread_id,))
        return self.store.transaction(write)

    def tail(self, thread_id, *, after_seq=0, limit=100, inline_payload_bytes=65536):
        if type(inline_payload_bytes) is not int or inline_payload_bytes < 0:
            raise ValueError('invalid inline payload budget')
        if type(limit) is not int or limit < 0:
            raise ValueError('invalid page limit')
        def read(view):
            head = _head(view, thread_id)
            rows = view.query('SELECT seq,kind,raw_bytes FROM thread_events WHERE thread_id=? AND seq>? ORDER BY seq LIMIT ?',
                              (thread_id,after_seq,limit), max_rows=limit)
            items = []
            for seq,kind,size in rows:
                item = {'seq':seq,'kind':kind}
                if size <= inline_payload_bytes:
                    item['payload'] = _load(view,thread_id,seq)
                else:
                    item['payload_ref'] = {'thread_id':thread_id,'seq':seq,'total_bytes':size,'read_method':'read_payload'}
                items.append(item)
            return {'items':items,
                    'next_seq':rows[-1][0] if rows else after_seq, 'total':head['last_seq']}
        return self.store.read(read)

    def read_payload(self, thread_id, seq, *, offset=0, size=65536):
        if type(offset) is not int or offset < 0 or type(size) is not int or size < 0:
            raise ValueError('invalid payload range')
        def read(view):
            rows = view.query('SELECT raw_bytes FROM thread_events WHERE thread_id=? AND seq=?', (thread_id,seq))
            if not rows:
                raise KeyError(seq)
            total = rows[0][0]
            end = min(total, offset+size)
            data = bytearray()
            if offset < end:
                for chunk,payload in view.iter_query('SELECT chunk,payload FROM thread_chunks WHERE thread_id=? AND seq=? AND chunk>=? AND chunk<=? ORDER BY chunk',
                                                     (thread_id,seq,offset//CHUNK_BYTES,(end-1)//CHUNK_BYTES)):
                    raw = zlib.decompress(payload)
                    start = chunk*CHUNK_BYTES
                    data.extend(raw[max(0,offset-start):min(len(raw),end-start)])
            return {'data':bytes(data),'total_bytes':total,'next_offset':min(total,max(offset,end))}
        return self.store.read(read)

    def list_threads(self, *, actor=None, after=0, limit=100):
        if type(limit) is not int or limit < 0:
            raise ValueError('invalid page limit')
        def read(view):
            where, bindings = ('ordinal>?', (after,)) if actor is None else ('actor=? AND ordinal>?', (actor,after))
            rows = view.query('SELECT id,ordinal FROM thread_heads WHERE '+where+' ORDER BY ordinal LIMIT ?', (*bindings,limit), max_rows=limit)
            count = view.query('SELECT total FROM thread_counts WHERE scope=?', ('all' if actor is None else 'actor:'+actor,))
            return {'items':[_head(view, item) for item,_ in rows], 'next':rows[-1][1] if rows else after,
                    'total':count[0][0] if count else 0}
        return self.store.read(read)
