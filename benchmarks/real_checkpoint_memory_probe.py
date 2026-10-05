"""原版本读取真实旧检查点；仅输出大小、耗时和 RSS，不写入来源运行。"""
from __future__ import annotations
import argparse
import asyncio
import gc
import gzip
import json
from pathlib import Path
import resource
import sys
import tempfile
import time


def rss():
    for line in Path('/proc/self/status').read_text().splitlines():
        if line.startswith('VmRSS:'):
            return int(line.split()[1]) * 1024
    raise RuntimeError('Linux VmRSS required')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('run', type=Path)
    parser.add_argument('--step', type=int, default=0)
    parser.add_argument('--mode', choices=['decompress', 'json', 'resolve', 'state', 'world'], required=True)
    parser.add_argument('--scratch', type=Path, required=True)
    args = parser.parse_args()
    from society0.incremental_checkpoint import V4CheckpointStore
    from society0.persistence import PersistenceManager
    import society0
    store = V4CheckpointStore(args.run)
    marker_path = args.run / 'checkpoints' / 'v4' / 'complete' / f'step_{args.step:06d}.json'
    marker = json.loads(marker_path.read_text())
    manifest_path = args.run / marker['manifest_file']
    record = {'step': args.step, 'checkpoint_id': marker['checkpoint_id'],
              'marker': marker, 'manifest': json.loads(manifest_path.read_text()),
              'marker_file': marker_path, 'manifest_file': manifest_path}
    component = args.run / record['manifest']['replacement_file']
    if component.suffix != '.gz':
        raise ValueError('This probe requires original gzip checkpoint and original reader')
    gc.collect()
    before = rss()
    peak_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    started = time.perf_counter()
    retained = None
    count = None
    with tempfile.TemporaryDirectory(dir=args.scratch, prefix='memory-probe-') as temp:
        if args.mode == 'decompress':
            count = 0
            with gzip.open(component, 'rb') as stream:
                while chunk := stream.read(1024 * 1024):
                    count += len(chunk)
        elif args.mode == 'json':
            with gzip.open(component, 'rt', encoding='utf-8') as stream:
                retained = json.load(stream)
            count = len(retained['entries'])
        elif args.mode == 'resolve':
            retained = store.resolve(args.step)
        elif args.mode == 'state':
            retained = store.restore(args.step)
        else:
            manager = PersistenceManager(temp)
            retained, _ = asyncio.run(manager._load_v4_checkpoint_record(record, event_logger=None, event_log_path=str(Path(temp) / "events.jsonl"), environment_factory=None))
            manager.close()
        elapsed = time.perf_counter() - started
        gc.collect()
        after = rss()
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
        print(json.dumps({'mode': args.mode, 'run': str(args.run), 'step': args.step,
            'checkpoint_id': record['checkpoint_id'], 'component': str(component.relative_to(args.run)),
            'compressed_bytes': component.stat().st_size, 'reader_version': society0.__version__,
            'reader_module': str(Path(sys.modules[V4CheckpointStore.__module__].__file__).resolve()),
            'count': count, 'seconds': elapsed, 'rss_before': before, 'rss_retained': after,
            'rss_delta': after-before, 'peak_rss': peak, 'peak_above_before': peak-before,
            'peak_before': peak_before}, ensure_ascii=False))

if __name__ == '__main__':
    main()
