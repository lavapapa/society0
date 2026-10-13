"""固定双源的同语义机制成本；仪表、无仪表计时与独立进程恢复分别测量。"""
import argparse
import asyncio
from contextlib import contextmanager, nullcontext
from dataclasses import asdict, is_dataclass
import json
import os
from pathlib import Path
import platform
import resource
import subprocess
import sys
import time
import tracemalloc
from unittest.mock import patch

import apsw


def disk(path):
    files = [p.stat() for p in path.rglob('*') if p.is_file()] if path.exists() else []
    return sum(s.st_size for s in files), sum(s.st_blocks * 512 for s in files)


def rss():
    return int(subprocess.check_output(['ps', '-o', 'rss=', '-p', str(os.getpid())], text=True).strip()) * 1024


class Meter:
    """复用既有 progress_handler、tracemalloc、ps/rusage 和目录字节口径。"""
    def __init__(self, path, measurement='instrumented'):
        self.path, self.phases, self.active = path, {}, None
        self.instrumented = measurement == 'instrumented'

    def connection(self, connection):
        if not self.instrumented:return
        def progress():
            if self.active is not None:self.active['sql_vm_steps'] += 1
            return False
        def row(cursor, values):
            if self.active is not None:
                for description, value in zip(cursor.get_description(), values):
                    if description[0] in ('body', 'content') and isinstance(value, (bytes, str)):
                        self.active['body_materialized_bytes'] += len(value.encode() if isinstance(value, str) else value)
            return values
        connection.set_progress_handler(progress, 1)
        connection.set_row_trace(row)

    @contextmanager
    def phase(self, name):
        from society0.kernel.storage import ReadView
        original = ReadView.read_blob
        row = {'sql_vm_steps': 0 if self.instrumented else None,
               'body_materialized_bytes': 0 if self.instrumented else None}
        def blob(view, *args, **kwargs):
            value = original(view, *args, **kwargs)
            row['body_materialized_bytes'] += len(value[0])
            return value
        before = disk(self.path); start_rss = rss()
        if self.instrumented:
            tracemalloc.start(); tracemalloc.reset_peak()
        wall, cpu = time.perf_counter(), time.process_time()
        self.active = row
        try:
            with patch.object(ReadView, 'read_blob', blob) if self.instrumented else nullcontext():yield row
        finally:
            row.update(wall_seconds=time.perf_counter()-wall, cpu_seconds=time.process_time()-cpu,
                       python_peak_bytes=tracemalloc.get_traced_memory()[1] if self.instrumented else None)
            self.active = None
            if self.instrumented:tracemalloc.stop()
            after = disk(self.path)
            row.update(rss_before_bytes=start_rss, rss_after_bytes=rss(),
                       process_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024),
                       disk_logical_delta=after[0]-before[0], disk_allocated_delta=after[1]-before[1])
            self.phases[name] = row


def plain(value):
    if is_dataclass(value):return plain(asdict(value))
    if isinstance(value, dict):return {k:plain(v) for k,v in value.items()}
    if isinstance(value, (list, tuple)):return [plain(v) for v in value]
    return value


def snapshot(environment, mode, details=()):
    if mode == 'rr':
        value=environment.conversation_view('c')
        value.pop('revision')
        return value
    return {'details':{str(key):environment.post_details(key) for key in details},
            'notices':environment.notifications('b',include_consumed=True),
            'recommended':{actor:environment.recommended_ids(actor) for actor in ('a','c')}}


def source_identity():
    import society0
    source=Path(society0.__file__).resolve().parents[2]
    return {'cwd':str(Path.cwd()),'source_root':str(source),'pythonpath':os.environ.get('PYTHONPATH'),
            'head':subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip(),
            'status':subprocess.check_output(['git','-C',str(source),'status','--short'],text=True)}


async def probe(path, *, mode, history, measurement='instrumented', resume_source=None, resume_step=None, source_result=None):
    from society0.kernel.actors import ActorRecord, actor_plugin
    from society0.kernel.composition import compose
    from society0.kernel.interaction import interaction_plugin, InteractionScope, Moment, Ref, Query
    from society0.kernel.plugins import Plugin
    from society0.plugins.round_robin import round_robin_plugin
    from society0.plugins.social import social_plugin
    from society0.plugins.social_models import SocialNetworkConfig
    from tests.primary.test_kernel_memory import Embed, Client
    from types import SimpleNamespace

    path = Path(path); meter = Meter(path,measurement); embed = Embed()
    body = '完整冷原文🙂' * 16384
    semantic = {}; counts = {'rank': 0}; embedding = mode == 'social_embedding'
    def resources(ctx):
        ctx.provide('embeddings', {'fixed': SimpleNamespace(embed=embed)})
        ctx.provide('vector_client', Client())
    def plugins():
        base = [actor_plugin({'rule': lambda record: None}, records=[ActorRecord(a, 'rule') for a in 'abcd']),
                interaction_plugin(lambda *args: True)]
        if mode == 'rr':return base + [round_robin_plugin('abcd', group_size=4, clock=lambda: 123.0)]
        config = SocialNetworkConfig(social_media={'recommendation': {
            'use_embedding_similarity': embedding, 'post_count': 2, 'full_scan_until': 4,
            'recent_keep_count': 4, 'top_engagement_keep_count': 2, 'min_lifetime_ticks': 0}})
        return base + [Plugin('vectors', install=resources), social_plugin('abcd', edges=[('a','b')], config=config,
            content_length_limit=-1, **({'embedding': ('vectors','fixed'), 'vector_client': ('vectors','vector_client')} if embedding else {}))]
    scope = lambda actor: InteractionScope(actor, Moment(history+10, 'cost'))
    async def continue_run(host, identifier):
        environment=host.service('conversation' if mode == 'rr' else 'social','mechanism')
        with meter.phase('continue'):
            if mode == 'rr':semantic['continuation']=plain(environment.advance_round())
            else:
                semantic['continuation']=plain(environment.execute('comment','c',identifier,{'content':'恢复后完整评论'},history+11))
                await environment.after_tick()
                semantic['continued_detail']=environment.post_details(identifier)
        with meter.phase('continued_complete'):host.service('storage','store').complete(3)
    if resume_source is not None:
        source_result=json.loads(source_result.read_text())
        assert source_result['source']['head']==source_identity()['head'], 'cold resume must import the same source version'
        assert (source_result['mode'],source_result['history'])==(mode,history)
        assert resume_step==2
        semantic=source_result['semantic'].copy()
        apsw.connection_hooks.append(meter.connection)
        try:
            manager=compose(path/'run',plugins(),source=resume_source,step=resume_step)
            with meter.phase('restore'):host=await manager.__aenter__()
            try:
                environment=host.service('conversation' if mode == 'rr' else 'social','mechanism')
                with meter.phase('snapshot'):
                    restored=snapshot(environment,mode,semantic.get('details',()))
                assert restored==source_result['snapshot_step2']
                await continue_run(host,source_result['business_id'])
                assert semantic==source_result['semantic']
            finally:
                with meter.phase('restored_close'):await manager.__aexit__(None,None,None)
        finally:apsw.connection_hooks.remove(meter.connection)
        with meter.phase('encoding'):
            encoded_bytes=len(json.dumps(plain(semantic),ensure_ascii=False).encode())
        return {'mode':mode,'history':history,'measurement':measurement,'resume_probe':True,'semantic_encoded_bytes':encoded_bytes,'phases':meter.phases,
                'restored_equal':True,'semantic':plain(semantic),'snapshot_step2':restored,
                'business_id':source_result['business_id'],'source_run':str(resume_source),'resume_step':resume_step,
                'limits':'Independent interpreter process; OS file cache retained. RSS peak is process lifetime. No real model/network.'}
    apsw.connection_hooks.append(meter.connection)
    try:
        manager = compose(path/'run', plugins())
        with meter.phase('create'):host = await manager.__aenter__()
        try:
            env = host.service('conversation' if mode == 'rr' else 'social', 'mechanism')
            store = host.service('storage','store'); info = host.service('interaction','information'); actions = host.service('interaction','actions')
            async def send(text):
                result = await actions.invoke(scope('a'), 'conversation.send_message_to_partner', Ref('conversation','participants','a'), {'content':text})
                assert result.status == 'completed'
                return result.value['message_id']
            with meter.phase('seed'):
                if mode == 'rr':
                    env.start_round(1)
                    for i in range(history):await send('历史原文 '+str(i))
                    env.start_round(2)
                    identifier = await send(body)
                else:
                    for i in range(history):
                        env.execute('publish_post','b','b',{'content':'历史原文 '+str(i)},i)
                    identifier = env.execute('publish_post','b','b',{'content':body},history+1).value['post_id']
                    for i in range(4):env.execute('publish_post','b','b',{'content':'活动完整帖子 '+str(i)},history+2)
                    await env.after_tick()
            with meter.phase('root_complete'):store.complete(1)
            with meter.phase('cold_small_action'):
                if mode == 'rr':
                    result = await actions.invoke(scope('a'),'conversation.mark_conversation_participant',Ref('conversation','participants','a'),{'marker':'ready'})
                else:result = env.execute('like_post','a',identifier,{},history+10)
                assert result.status == 'completed'
            with meter.phase('full_original'):
                actor = 'c' if mode == 'rr' else 'a'
                route = '/conversation/content/' if mode == 'rr' else '/social/content/'
                parts=[]; offset=0
                while True:
                    part=await info.read(scope(actor),route+str(identifier),offset=offset,size=65536)
                    parts.append(part.data)
                    if part.next_offset is None:break
                    offset=part.next_offset
                assert b''.join(parts)==body.encode()
                semantic['original']=b''.join(parts).decode()
            if mode != 'rr':
                owner = env.recommendations
                original_rank = owner.rank
                def rank(*args,**kwargs):
                    counts['rank']+=1
                    return original_rank(*args,**kwargs)
                owner.rank=rank
            calls_before=len(embed.calls)
            with meter.phase('hot'):
                if mode == 'rr':
                    semantic['pairing']=env.pairing('a')
                    page=await info.query(scope('c'),'/conversation/messages',Query(limit=2))
                    semantic['page']={'items':plain(page.items),'total':page.total}
                else:
                    cursors={}; pages=[]
                    for actor in ('a','c','a','c'):
                        page=await info.query(scope(actor),'/social/feed',Query(limit=1,cursor=cursors.get(actor)))
                        cursors[actor]=page.next_cursor
                        pages.append({'actor':actor,'items':plain(page.items),'total':page.total})
                    semantic['pages']=pages
            hot_embeddings=len(embed.calls)-calls_before
            assert counts['rank']==(0 if mode=='rr' else 2)
            assert hot_embeddings==(2 if embedding else 0)
            meter.phases['hot'].update(rank_calls=counts['rank'],embedding_calls=hot_embeddings)
            with meter.phase('presentation'):
                if mode != 'rr':
                    semantic['shown']={actor:await env.recommended_feed(actor,history+10) for actor in ('a','c')}
                    await env.after_tick()
                    semantic['exposure']={actor:env.recommended_ids(actor) for actor in ('a','c')}
                    semantic['details']={key:env.post_details(key) for key in set(sum(semantic['exposure'].values(),[]))}
                else:semantic['continued_message']=await send('完整续写消息')
            with meter.phase('complete'):store.complete(2)
            with meter.phase('snapshot'):
                expected=snapshot(env,mode,semantic.get('details',()))
        finally:
            with meter.phase('close'):await manager.__aexit__(None,None,None)
        manager=compose(path/'restored',plugins(),source=path/'run')
        with meter.phase('restore'):host=await manager.__aenter__()
        try:
            env=host.service('conversation' if mode == 'rr' else 'social','mechanism')
            restored_equal=snapshot(env,mode,semantic.get('details',()))==expected
            assert restored_equal
            await continue_run(host,identifier)
        finally:
            with meter.phase('restored_close'):await manager.__aexit__(None,None,None)
    finally:apsw.connection_hooks.remove(meter.connection)
    with meter.phase('encoding'):
        semantic=plain(semantic)
        encoded_bytes=len(json.dumps(semantic,ensure_ascii=False).encode())
    return {'mode':mode,'history':history,'measurement':measurement,'resume_probe':False,'semantic_encoded_bytes':encoded_bytes,'body_bytes':len(body.encode()),'phases':meter.phases,
            'snapshot_step2':expected,'business_id':identifier,
            'restored_equal':restored_equal,'semantic':plain(semantic),'embedding_calls_total':len(embed.calls),
            'platform':platform.platform(),'python':platform.python_version(),
            'limits':'Instrumented mode CPU/wall include per-VM callback and tracemalloc; timing mode installs no VM handler, row trace, blob patch or tracemalloc, unmeasured fields are null. RSS peak is process lifetime, not phase peak. Instrumented body bytes count SQL body/content cells plus blob reads, not disk IO. Hot restore uses new connections in same process; OS cache retained. Setup history uses canonical writers. No real model/network. Per-version semantic roots; cross-schema restore intentionally unsupported.'}


def compare(before, after):
    assert (before['mode'],before['history']) == (after['mode'],after['history'])
    assert before['semantic'] == after['semantic'], 'business information, scores, order, exposure or originals differ'
    return {'semantic_equal':True,'mode':before['mode'],'history':before['history'],
            'before':before['phases'],'after':after['phases']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=['rr','social','social_embedding'])
    parser.add_argument('--history',type=int,default=4)
    parser.add_argument('--root',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--compare',nargs=2,type=Path)
    parser.add_argument('--measurement',choices=['instrumented','timing'],default='instrumented')
    parser.add_argument('--resume-source',type=Path)
    parser.add_argument('--resume-step',type=int)
    parser.add_argument('--source-result',type=Path)
    args=parser.parse_args()
    resume=(args.resume_source,args.resume_step,args.source_result)
    if any(item is not None for item in resume) and not all(item is not None for item in resume):
        parser.error('--resume-source, --resume-step and --source-result required together')
    if args.compare:
        result=compare(*(json.loads(p.read_text()) for p in args.compare))
    else:
        if not args.mode or not args.root:parser.error('--mode and --root required for a probe')
        result=asyncio.run(probe(args.root,mode=args.mode,history=args.history,measurement=args.measurement,
            resume_source=args.resume_source,resume_step=args.resume_step,source_result=args.source_result))
        result['source']=source_identity()
        if args.measurement=='timing':
            assert all(phase['sql_vm_steps'] is None and phase['body_materialized_bytes'] is None and phase['python_peak_bytes'] is None
                       for phase in result['phases'].values())
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps({'output':str(args.output),'semantic_equal':result.get('semantic_equal'),'mode':result['mode'],'history':result['history']}))


if __name__=='__main__':main()
