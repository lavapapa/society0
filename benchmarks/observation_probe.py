"""可重复的查询规模与历史增长测量；临时产物随进程退出清理。"""
import argparse
import json
from pathlib import Path
import resource
import tempfile
import time

from society0.incremental_checkpoint import V4CheckpointStore, SealedTickDelta
from society0.observation import ObservationReader


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--records', type=int, default=204882)
    parser.add_argument('--value-bytes', type=int, default=550)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='society0-query-probe-') as temporary:
        root = Path(temporary)
        store = V4CheckpointStore(root)
        before = time.perf_counter()
        marker = store.publish_root(({'sequence': i, 'operation': 'set',
            'path': ['environment','state','entries',str(i)], 'value': 'x' * args.value_bytes}
            for i in range(args.records)), metadata={'run_id':'query-probe'})
        write_seconds = time.perf_counter()-before
        with ObservationReader(root, index_dir=root/'local-index') as reader:
            before = time.perf_counter()
            reader.sync()
            initial_index_seconds = time.perf_counter()-before
            samples = []
            for _ in range(20):
                before = time.perf_counter()
                page = reader.state_page(path=['environment','state','entries'],limit=100)
                samples.append(time.perf_counter()-before)
            increments = []
            for step in range(1,21):
                store.publish(SealedTickDelta(step, ({'sequence':0,'operation':'set',
                    'path':['environment','state','entries','0'],'value':'updated'},), ()))
                before = time.perf_counter()
                reader.sync()
                increments.append(time.perf_counter()-before)
            before_checkpoint = {p.name:p.stat().st_size for p in (root/'local-index').iterdir() if p.is_file()}
            reader.db.execute('PRAGMA wal_checkpoint(PASSIVE)')
            print(json.dumps({'dbstat':dict(reader.db.execute('SELECT name,sum(pgsize) FROM dbstat GROUP BY name')), 'table_rows':{name:reader.db.execute('SELECT count(*) FROM '+name).fetchone()[0] for name in ('entries','scopes','paths','counts')}, 'index_files_before_passive_checkpoint':before_checkpoint,'records':args.records,'value_bytes':args.value_bytes,
                'write_seconds':write_seconds,'initial_index_seconds':initial_index_seconds,
                'page_samples_seconds':samples,'page_p95_seconds':sorted(samples)[18],
                'increment_samples_seconds':increments,'total':page['total'],
                'process_peak_rss_native_units':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                'index_files_bytes':{p.name:p.stat().st_size for p in (root/'local-index').iterdir() if p.is_file()},
                'temporary_artifacts_removed':True},indent=2))

if __name__ == '__main__':
    main()
