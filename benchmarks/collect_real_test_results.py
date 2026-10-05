"""从测试自有工件提取数字和结果；不复制提示词、模型正文或凭据。"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import xml.etree.ElementTree as ET


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    parser.add_argument('--prefix', choices=['baseline', 'final', 'focused', 'verified', 'verifiedv3'], required=True)
    args = parser.parse_args()
    suite = ET.parse(args.root / (args.prefix+'.xml')).getroot().find('testsuite')
    tests=[]
    for row in suite.findall('testcase'):
        failure=row.find('failure')
        tests.append({'name':row.attrib['name'],'seconds':float(row.attrib['time']),
            'status':'failed' if failure is not None else 'skipped' if row.find('skipped') is not None else 'passed',
            **({'failure':failure.attrib.get('message','')} if failure is not None else {})})
    runs=[]
    for p in sorted((args.root/('artifacts-'+args.prefix)).rglob('resource_calls.jsonl')):
        if any(part.is_symlink() for part in p.parents if part != args.root):
            continue
        counts=Counter();durations=defaultdict(list)
        for line in p.read_text().splitlines():
            row=json.loads(line);kind=row.get('resource_type','unknown')
            counts[kind+':'+str(row.get('status'))]+=1
            if row.get('status')=='success':
                for field in ['provider_duration_sec','queue_duration_sec','duration_sec']:
                    value=row.get(field)
                    if isinstance(value,(float,int)):durations[kind+':'+field].append(value)
        summary=p.parent/'summary.json'
        data=json.loads(summary.read_text()) if summary.exists() else {}
        runs.append({'run':str(p.parent.relative_to(args.root)), 'calls':dict(counts),
            'resource_timings':{k:{'count':len(v),'sum':sum(v),'min':min(v),'max':max(v)} for k,v in durations.items()},
            'run_total_time':data.get('total_time'),'steps_completed':data.get('steps_completed')})
    print(json.dumps({'prefix':args.prefix,'tests':tests,'counts':dict(Counter(t['status'] for t in tests)),
        'suite_seconds':float(suite.attrib['time']),'runs':runs,
        'resource_scope':'Run resource logs count completed provider calls; direct smoke/saturation managers are captured by final stage plugin, and retries remain separately reported.'},indent=2))

if __name__=='__main__':main()
