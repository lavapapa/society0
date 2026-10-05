"""对固定研究基线fc67432运行相同记录集；只读取git对象。"""
import importlib.util
import json
import random
import resource
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    source = root / 'baseline.py'
    source.write_bytes(subprocess.check_output(['git', 'show', 'fc67432:src/society0/incremental_checkpoint.py']))
    spec = importlib.util.spec_from_file_location('society0._baseline_probe', source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    current = '--current' in sys.argv
    if current:
        from society0 import incremental_checkpoint as module
    small, large = 'x' * 580, 'z' * 2925528
    varied = '--varied' in sys.argv
    rng = random.Random(17)
    def value(i):
        if i == 204881:
            return large
        if not varied:
            return small
        return {'firm_id': f'firm-{i % 350}', 'day': i // 350, 'quantity': (i * 7) % 123456,
                'memo': '经营观察' * 21 + rng.randbytes(128).hex()}
    entries = tuple({'sequence': i, 'path': ['environment', 'state', 'entries', str(i)],
                     'operation': 'set', 'value': value(i)} for i in range(204882))
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    started = time.perf_counter()
    marker = module.V4CheckpointStore(root / 'run').publish_root(entries, metadata={'run_id': 'baseline'})
    elapsed = time.perf_counter()-started
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    table_bytes = {}
    raw_total = raw_max = 0
    for entry in entries:
        size = len(json.dumps(entry, ensure_ascii=False, separators=(',', ':')).encode())
        raw_total += size
        raw_max = max(raw_max, size)
    if current:
        manifest = json.loads((root / 'run' / marker['manifest_file']).read_text())
        with sqlite3.connect(root / 'run' / manifest['replacement_file']) as db:
            raw_total, raw_max = db.execute('SELECT sum(raw_bytes),max(raw_bytes) FROM records').fetchone()
            table_bytes = dict(db.execute('SELECT name,sum(pgsize) FROM dbstat GROUP BY name'))
    print(json.dumps({'variant': 'structured' if varied else 'repeated', 'record_raw_bytes_total': raw_total, 'record_raw_bytes_max': raw_max, 'table_bytes': table_bytes, 'commit': 'working-tree' if current else 'fc67432', 'records': len(entries), 'publish_seconds': elapsed,
        'rss_before_bytes_macos': before, 'rss_peak_bytes_macos': peak,
        'rss_additional_bytes_macos': peak-before, 'bytes_written': marker['bytes_written']}, indent=2))
