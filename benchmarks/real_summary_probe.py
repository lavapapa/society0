"""在真实测试工件副本上重算总结，拆分等待；不调用模型或改原测试目录。"""
from __future__ import annotations
import argparse
import asyncio
from collections import defaultdict
import functools
import json
import os
from pathlib import Path
import shutil
import tempfile
import time


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('source',type=Path)
    parser.add_argument('--scratch',type=Path,required=True)
    args=parser.parse_args()
    from society0 import Society0
    from society0 import result_datasets
    from tests.e2e.test_society0_real_e2e import _saturation_agent_config
    original_summary = result_datasets.load_history(args.source, json.loads((args.source / "summary.json").read_text()))
    rows=defaultdict(list)
    def timed(label,fn):
        @functools.wraps(fn)
        def call(*a,**kw):
            start=time.perf_counter()
            try:return fn(*a,**kw)
            finally:rows[label].append(time.perf_counter()-start)
        return call
    with tempfile.TemporaryDirectory(prefix='summary-profile-',dir=args.scratch) as folder:
        run=Path(folder)/'run'
        shutil.copytree(args.source,run)
        # 此场景步骤小表均内嵌；移除副本内上次总结的派生数据集。
        shutil.rmtree(run/'result_datasets',ignore_errors=True)
        engine=Society0(str(run),base_config=_saturation_agent_config(6),checkpoint_every=1)
        engine.current_world_state=engine._create_initial_world()
        engine.current_world_state.step=1
        engine.current_world_state.set_function_registry(engine.registry)
        for name in ['_summarize_agent_operations','_summarize_events','_summarize_resource_calls',
                     '_summarize_capabilities','_summarize_output_files','_write_diagnostics_report','_write_summary_payload']:
            setattr(engine,name,timed(name,getattr(engine,name)))
        result_datasets.write_dataset=timed('write_dataset',result_datasets.write_dataset)
        result_datasets.write_records=timed('write_records',result_datasets.write_records)
        os.fsync=timed('os.fsync',os.fsync)
        start=time.perf_counter()
        asyncio.run(engine._save_summary(steps_requested=1,steps_completed=1,total_time=0))
        elapsed=time.perf_counter()-start
        engine.persistence_manager.close()
        restored = result_datasets.load_history(run, json.loads((run / 'summary.json').read_text()))
        equality = {name: restored[name] == original_summary[name]
                    for name in ('agent_operations', 'resources', 'events')}
        assert all(equality.values()), equality
        print(json.dumps({'source':str(args.source),'scope':'summary replay from copy; no LLM calls',
            'seconds':elapsed,'history_equal':equality,'stages':{k:{'count':len(v),'sum':sum(v),'max':max(v)} for k,v in rows.items()}}))

if __name__=='__main__':main()
