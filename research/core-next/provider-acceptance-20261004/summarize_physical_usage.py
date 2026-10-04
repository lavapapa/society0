"""按显式清单只读汇总物理调用；共同历史去重，旧失真统计不进入token合计。"""
import argparse
import json
from pathlib import Path
import tempfile
from society0.kernel.storage import StageReader,StageStore
from society0.kernel.usage import COUNTERS


def summarize(entries):
    calls={};snapshots=[]
    for entry in entries:
        source=Path(entry['path']);valid=entry['usage_valid']
        if type(valid) is not bool:raise ValueError('usage_valid must be explicit boolean')
        with tempfile.TemporaryDirectory() as td:
            selected=source
            if entry.get('step') is not None:
                selected=Path(td)/'complete'
                StageStore.prepare_readonly(source,selected,step=entry['step'])
            with StageReader(selected) as reader:
                tables={x[0] for x in reader.read(lambda v:v.query("SELECT name FROM sqlite_master WHERE type='table'"))}
                count=0
                for prefix in ('thread','resource'):
                    table=prefix+'_usage_calls'
                    if table not in tables:continue
                    rows=reader.read(lambda v:list(v.iter_query(f'SELECT id,model,{",".join(COUNTERS)} FROM {table}')))
                    for identifier,model,*values in rows:
                        key=(prefix,identifier);facts=dict(zip(COUNTERS,values));count+=1
                        if key in calls:
                            old=calls[key]
                            if old['model']!=model or old['facts']!=facts or old['usage_valid']!=valid:
                                raise ValueError('same physical identity has inconsistent selected facts: '+repr(key))
                            old['sources'].append(str(source))
                        else:calls[key]={'kind':prefix,'id':identifier,'model':model,'facts':facts,'usage_valid':valid,'sources':[str(source)]}
                snapshots.append({'path':str(source),'step':entry.get('step'),'scope':'complete' if entry.get('step') is not None else 'current_diagnostic','usage_valid':valid,'selected_calls':count})
    groups={}
    for call in calls.values():
        key=(call['kind'],call['model'],call['usage_valid'])
        group=groups.setdefault(key,{'kind':key[0],'model':key[1],'usage_valid':key[2],'physical_calls':0,'requests':0,'responses':0,'errors':0,'cancelled':0,'input_tokens':0,'output_tokens':0,'cache_read_tokens':0,'input_reports':0,'output_reports':0,'cache_read_reports':0,'cache_write_reports':0})
        group['physical_calls']+=1
        for field in ('requests','responses','errors','cancelled'):group[field]+=call['facts'][field]
        if call['usage_valid']:
            for field in ('input_tokens','output_tokens','cache_read_tokens','input_reports','output_reports','cache_read_reports','cache_write_reports'):group[field]+=call['facts'][field]
    for group in groups.values():
        if not group['usage_valid']:
            for key in list(group):
                if key.endswith('_tokens') or key.endswith('_reports'):group[key]=None
        # Cache比例只对同组LLM输入，并保留缺失报告范围。
        group['cache_read_fraction']=group['cache_read_tokens']/group['input_tokens'] if group['usage_valid'] and group['kind']=='thread' and group['input_tokens'] else None
    return {'snapshots':snapshots,'groups':list(groups.values()),'unique_calls':list(calls.values()),'limits':'Explicit selected snapshots only. Shared IDs count once. Invalid historical SDK totals excluded from aggregates; raw facts retained for diagnosis. No billing estimate; partial-response usage is not substituted for missing terminal reports.'}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--manifest',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    result=summarize(json.loads(a.manifest.read_text()))
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
