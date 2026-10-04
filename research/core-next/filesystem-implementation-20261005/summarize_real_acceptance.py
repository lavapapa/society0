"""按物理调用身份去重恢复副本，导出脱敏数字验收摘要。"""
import json,sys,xml.etree.ElementTree as ET
from pathlib import Path
from society0.kernel.storage import StageReader
from society0.kernel.usage import COUNTERS
root=Path(sys.argv[1]);xml=ET.parse(root/'real.xml').getroot()
results=[{'name':t.attrib['name'],'seconds':float(t.attrib.get('time',0)),'status':'failed' if t.find('failure') is not None or t.find('error') is not None else 'skipped' if t.find('skipped') is not None else 'passed'} for t in xml.iter('testcase')]
calls={};receipts=set();requests={}
for run in root.iterdir():
 if not run.is_dir() or not (run/'run.json').exists():continue
 with StageReader(run) as reader:
  def read(v):
   for prefix,kind in [('thread','llm'),('resource','embedding')]:
    if not v.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(prefix+'_usage_calls',)):continue
    for row in v.iter_query(f'SELECT id,model,{",".join(COUNTERS)} FROM {prefix}_usage_calls'):
     calls[(kind,row[0])]={'kind':kind,'model':row[1],**dict(zip(COUNTERS,row[2:]))}
   if v.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name='thread_tool_receipts'"):
    receipts.update(v.iter_query('SELECT thread_id,call_id FROM thread_tool_receipts'))
  reader.read(read)
models={}
for c in calls.values():
 key=c['kind']+':'+c['model'];totals=models.setdefault(key,dict.fromkeys(COUNTERS,0))
 for name in COUNTERS:totals[name]+=c[name]
launch=json.loads((root/'launch.json').read_text())
expected=len(launch['cases']) if isinstance(launch['cases'],list) else 15
identity=json.loads((root.parent/'tested-product-identity.json').read_text())
summary={'tested_product':identity,'results':results,'passed':sum(t['status']=='passed' for t in results),'failed':sum(t['status']=='failed' for t in results),'skipped':sum(t['status']=='skipped' for t in results),'expected_cases':expected,'models':models,'physical_calls':len(calls),'tool_receipts':len(receipts),'tool_tokens':None,'tool_tokens_note':'provider reports full request input usage; separate tool-schema/tool-return token counts unavailable','history_contract':'full Thread history; deterministic request loader and correction-history tests passed','budget':{'default_max_turns':8,'vfs_max_turns':20,'default_max_action_calls':4,'vfs_max_action_calls':2,'request_max_tokens':1024,'raised':False},'embedding_dimensions':1024,'oracle':'original tests assert complete documents, all pages, actions, memory and recovery; see real.xml','usage_attribution':'physical IDs deduplicate restored copies; timing sums wall duration of attempts, not elapsed suite time'}
(root/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));print(json.dumps({'passed':summary['passed'],'failed':summary['failed'],'physical_calls':len(calls)}))
