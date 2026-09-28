"""通过真实World→PersistenceManager.publish_root验证根发布内存。"""
import asyncio
import json
import resource
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from society0.core_data import World
from society0.persistence import PersistenceManager
from society0.incremental_checkpoint import PersistenceSchema

async def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        world = World(event_log_path=str(root / 'events.jsonl'))
        # 不同对象和键；字符串共享模拟不可变payload引用，单独报告World常驻成本。
        payload = 'z' * 9160
        world.environment_data['state'] = {'entities': {str(i): {'payload': payload} for i in range(204882)}}
        schema = PersistenceSchema.compile({'type': 'object', 'properties': {'entities': {
            'type': 'object', 'additionalProperties': {'type': 'object'},
            'persistence': {'kind': 'replaceable', 'granularity': 'entry'}}}}, root_path=('environment', 'state'))
        manager = PersistenceManager(str(root / 'run'))
        manager.configure_v4(world, schema)
        def reject_snapshot(*args):
            raise AssertionError('root copied whole World')
        manager._plain_snapshot_value = reject_snapshot
        before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        started = time.perf_counter()
        marker = await manager.publish_root(world, object())
        seconds = time.perf_counter() - started
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        lookup_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        lookup_start = time.perf_counter()
        manager._v4_store.resolve(0)
        lookup_seconds = time.perf_counter() - lookup_start
        lookup_peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        configure_start = time.perf_counter()
        manager.configure_v4(world, schema)
        configure_seconds = time.perf_counter() - configure_start
        discard_start = time.perf_counter()
        manager.discard_unpublished_epoch()
        discard_seconds = time.perf_counter() - discard_start
        manifest = json.loads((manager.save_dir / marker['manifest_file']).read_text())
        db = sqlite3.connect(manager.save_dir / manifest['replacement_file'])
        total, maximum, count = db.execute('SELECT sum(raw_bytes),max(raw_bytes),count(*) FROM records').fetchone()
        db.close()
        cold_code = """import json,resource,sys,time
from society0.incremental_checkpoint import V4CheckpointStore
store=V4CheckpointStore(sys.argv[1],create=False)
before=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
start=time.perf_counter();result=store.resolve(0);elapsed=time.perf_counter()-start
peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
unit=1 if sys.platform=='darwin' else 1024
print(json.dumps({'seconds':elapsed,'rss_before_bytes':before*unit,'rss_peak_bytes':peak*unit,'rss_extra_peak_bytes':(peak-before)*unit,'step':result['step']}))
"""
        cold_lookup = json.loads(subprocess.check_output([sys.executable, '-c', cold_code, str(manager.save_dir)]))
        manager.close()
        world.event_logger.close()
        print(json.dumps({'route': 'World -> PersistenceManager.publish_root', 'records': count,
            'record_raw_bytes_total': total, 'record_raw_bytes_max': maximum, 'publish_seconds': seconds,
            'rss_before_bytes_macos': before, 'rss_peak_bytes_macos': peak,
            'rss_additional_bytes_macos': peak-before, 'bytes_written': marker['bytes_written'],
            'world_payload_strings_shared': True, 'whole_world_snapshot_forbidden': True,
            'identity_resolve_seconds': lookup_seconds, 'identity_resolve_extra_high_water_rss_bytes': lookup_peak - lookup_before, 'independent_process_identity': cold_lookup,
            'configure_existing_seconds': configure_seconds, 'discard_seconds': discard_seconds}, indent=2))
asyncio.run(main())
