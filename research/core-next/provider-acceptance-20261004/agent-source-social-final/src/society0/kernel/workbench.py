"""将显式选择的完整结果与 Thread 原文转换为现有工作台数据。"""
from dataclasses import dataclass
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory

from .storage import StageStore
from .results import Results
from .threads import ThreadStore


@dataclass(frozen=True)
class RunSelection:
    path: Path
    version: str
    steps: tuple[int, ...]
    actors: tuple[str, ...]


def _json(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False)


def _rows(results,reference):
    cursor=None
    while True:
        page=results.page(reference,cursor=cursor,limit=1000,max_bytes=1048576)
        for item in page['items']:
            if 'value' in item:
                yield item['value']
                continue
            chunks=[];offset=0
            while True:
                part=results.read_record(item['payload_ref'],offset=offset,size=65536)
                chunks.append(part['data']);offset+=len(part['data'])
                if offset>=part['total_bytes']:break
            yield json.loads(b''.join(chunks))
        cursor=page['next_cursor']
        if cursor is None:break


def _module(identifier,title,values,source):
    values=list(values)
    views=[{'id':'original','type':'text','title':'完整记录','paragraphs':[_json(v) for v in values]}]
    if values and all(isinstance(value,dict) for value in values):
        keys=dict.fromkeys(key for value in values for key in value)
        if keys:
            views.append({'id':'table','type':'table','title':'表格',
                'columns':[{'key':key,'label':key} for key in keys],
                'rows':[{key:_json(value[key]) if key in value else '' for key in keys} for value in values]})
    return {'id':identifier,'title':title,'source':source,'views':views}


def _sessions(reader,threads,actor,moment,step,source):
    key=json.dumps(moment,sort_keys=True,allow_nan=False)
    heads=reader.read(lambda r:list(r.iter_query('SELECT id,kind FROM thread_heads WHERE actor=? AND moment=? ORDER BY ordinal',(actor,key))))
    sessions=[]
    for tid,kind in heads:
        rows=reader.read(lambda r:r.query('SELECT seq FROM thread_events WHERE thread_id=? AND publish_step<=? ORDER BY publish_step DESC,seq DESC LIMIT 1',(tid,step)))
        if not rows:continue
        through=rows[0][0];after=0;events=[]
        while after<through:
            metadata=reader.read(lambda r:r.query('SELECT seq,kind FROM thread_events WHERE thread_id=? AND seq>? AND seq<=? ORDER BY seq LIMIT 128',(tid,after,through)))
            for seq,event_kind in metadata:
                chunks=[];offset=0
                while True:
                    part=threads.read_payload(tid,seq,offset=offset,size=65536)
                    chunks.append(part['data']);offset=part['next_offset']
                    if offset>=part['total_bytes']:break
                payload=json.loads(b''.join(chunks))
                events.append({'id':str(seq),'kind':'observation','title':event_kind,'data':payload,
                               'source':f'{source} · Thread {tid} · seq {seq}'})
                after=seq
        sessions.append({'id':tid,'label':moment['phase']+' · '+kind,
            'description':f'完整步骤 {step}，事件前缀 1–{through}','events':events})
    return sessions


def _export(selection):
    path=Path(selection.path).resolve()
    manifest=json.loads((path/'runner.json').read_text())
    steps=tuple(selection.steps);actors=tuple(selection.actors)
    if not steps or any(type(step) is not int or step<0 for step in steps):
        raise ValueError('export requires explicit complete steps')
    if len(set(steps))!=len(steps) or len(set(actors))!=len(actors):
        raise ValueError('duplicate step or actor selection')
    with TemporaryDirectory(prefix='society0-workbench-') as temporary:
        with StageStore.prepare_readonly(path,Path(temporary)/'view',step=max(steps)) as reader:
            source_run=json.loads((path/'run.json').read_text())['run_id']
            if source_run!=manifest['run_id']:raise ValueError('runner identity differs from run')
            tables=reader.read(lambda r:{row[0] for row in r.iter_query("SELECT name FROM sqlite_master WHERE type='table'")})
            results=Results(reader);threads=ThreadStore(reader)
            entities=[{'id':'environment','name':'共享环境','kind':'environment'},*[{'id':'actor:'+a,'name':a,'kind':'actor'} for a in actors]]
            run={'id':source_run,'ticks':[],'snapshots':[],
                'source':str(path),'complete_step':max(steps),'export_scope':{'steps':list(steps),'actors':list(actors),'content':'results_and_thread_events'}}
            trends={}
            for step in sorted(steps):
                detail=results.step(step)
                run['ticks'].append({'id':str(step),'label':str(detail['time'])})
                phases=reader.read(lambda r:list(r.iter_query('SELECT ordinal,name,elapsed_s FROM result_phases WHERE step=? ORDER BY ordinal',(step,))))
                environment=[];per_actor={a:[] for a in actors};sessions={a:[] for a in actors}
                for ordinal,name,elapsed in phases:
                    header=results.phase(step,ordinal);source=f'{path} · step {step} · phase {ordinal}'
                    metrics=list(_rows(results,header['metrics']))
                    environment.append(_module(f'{ordinal}:header',name+' 阶段',[
                        {'elapsed_s':elapsed,'artifacts':header['artifacts'],'observations':header['observations'],'notes':header['notes']},
                        *metrics],source))
                    for metric in metrics:
                        value=metric['value']
                        if type(value) not in (int,float) or abs(value)>2**53 or not math.isfinite(value):continue
                        points=trends.setdefault((ordinal,name,metric['name']),[])
                        points.append({'x':str(step)+' · '+str(detail['time']),'y':value})
                        environment.append({'id':f'{ordinal}:metric:'+metric['name'],'title':name+' / '+metric['name'],'source':source,
                            'views':[{'id':'trend','type':'timeseries','title':'已选完整步骤趋势','rows':list(points),'maxPoints':len(points)}]})
                    for label,reference in header['tables'].items():
                        environment.append(_module(f'{ordinal}:table:{label}',name+' / '+label,_rows(results,reference),source))
                    for row in _rows(results,header['activations']):
                        actor=row['actor_id']
                        if actor in per_actor:per_actor[actor].append(_module(f'{ordinal}:{row["round"]}',name+' 激活', [row],source))
                    if 'thread_heads' in tables:
                        for actor in actors:sessions[actor].extend(_sessions(reader,threads,actor,{'time':detail['time'],'phase':name},step,path))
                if step==max(steps):
                    from . import action_stats,usage
                    for actor,modules in ((None,environment),*per_actor.items()):
                        values=[]
                        if 'thread_usage_totals' in tables or 'resource_usage_totals' in tables:
                            values.append(reader.read(lambda r:usage.read(r,actor=actor)))
                        if 'thread_action_totals' in tables:
                            values.append(reader.read(lambda r:action_stats.read(r,actor=actor)))
                        if values:modules.append(_module('cumulative','截至完整步骤 '+str(step)+' 的累计',values,str(path)))
                run['snapshots'].append({'tickId':str(step),'entityId':'environment','tabs':[{'id':'results','title':'运行结果','modules':environment}]})
                for actor in actors:
                    run['snapshots'].append({'tickId':str(step),'entityId':'actor:'+actor,'sessions':sessions[actor],
                        'tabs':[{'id':'results','title':'激活结果','modules':per_actor[actor]}]})
                    if sessions[actor]:next(entity for entity in entities if entity['id']=='actor:'+actor)['kind']='llm-agent'
            return {'id':selection.version,'name':selection.version,'config':manifest['contract'],
                    'configSource':str(path/'runner.json'),'entities':entities,'runs':[run]}


def export_payload(selections,*,title='Society0 工作台',question=''):
    payload={'study':{'title':title,'question':question},'versions':[]}
    versions={}
    for selection in selections:
        version=_export(selection)
        if version['id'] in versions:
            previous=versions[version['id']]
            if previous['config']!=version['config'] or previous['entities']!=version['entities']:
                raise ValueError('configuration differs within one version')
            previous['runs'].extend(version['runs'])
        else:
            versions[version['id']]=version;payload['versions'].append(version)
    return payload


def main():
    import argparse
    parser=argparse.ArgumentParser(description='导出明确选择的完整运行结果与会话，供现有工作台渲染')
    parser.add_argument('--run',required=True,type=Path)
    parser.add_argument('--version',required=True)
    parser.add_argument('--steps',required=True,type=int,nargs='+')
    parser.add_argument('--actors',nargs='*',default=[])
    parser.add_argument('--title',default='Society0 工作台')
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    try:
        payload=export_payload([RunSelection(args.run,args.version,tuple(args.steps),tuple(args.actors))],title=args.title)
        with args.output.open('w',encoding='utf-8') as stream:
            json.dump(payload,stream,ensure_ascii=False,allow_nan=False,separators=(',',':'))
    except Exception as error:
        parser.exit(1,json.dumps({'error':{'code':type(error).__name__}},ensure_ascii=False)+'\n')


if __name__=='__main__':main()
