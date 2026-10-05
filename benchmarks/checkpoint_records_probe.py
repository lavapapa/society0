"""检查点记录编码的独立进程量级验收；不读取实验工件。"""
import argparse
import json
import resource
import sqlite3
import tempfile
import time
from pathlib import Path
from society0.checkpoint_records import write_records, read_page, iter_record_bytes

parser = argparse.ArgumentParser()
parser.add_argument('--count', type=int, default=204882)
parser.add_argument('--value-bytes', type=int, default=580)
parser.add_argument('--large-bytes', type=int, default=2925528)
args = parser.parse_args()
small = 'x' * args.value_bytes
large = 'z' * args.large_bytes
before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / 'records.sqlite'
    def records():
        for i in range(args.count):
            yield {'sequence': i, 'path': ['environment', 'state', 'entries', str(i)],
                   'operation': 'set', 'value': large if i == args.count - 1 else small}
    started = time.perf_counter()
    write_records(path, records())
    elapsed = time.perf_counter() - started
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    with sqlite3.connect(path) as db:
        raw_total, raw_max = db.execute('SELECT sum(raw_bytes), max(raw_bytes) FROM records').fetchone()
    query_started = time.perf_counter()
    page = read_page(path, after_sequence=args.count - 3, limit=2, max_bytes=1000)
    query_elapsed = time.perf_counter() - query_started
    range_started = time.perf_counter()
    range_bytes = sum(map(len, iter_record_bytes(path, args.count-1, offset=args.large_bytes - 50, max_bytes=50)))
    print(json.dumps({'records': args.count, 'value_bytes': args.value_bytes,
        'large_value_bytes': args.large_bytes, 'payload_input_bytes': (args.count-1)*args.value_bytes+args.large_bytes,
        'record_raw_bytes_total': raw_total, 'record_raw_bytes_max': raw_max, 'write_seconds': elapsed, 'rss_before': before, 'rss_peak': peak,
        'rss_additional_bytes_macos': peak-before, 'file_bytes': path.stat().st_size,
        'tail_page_seconds': query_elapsed, 'range_seconds': time.perf_counter()-range_started,
        'range_bytes': range_bytes, 'page_payload_bytes': page['payload_bytes']}, indent=2))
