"""通过真实World→PersistenceManager.publish_root验证根发布内存。"""
import asyncio
import json
import resource
import sqlite3
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
        manifest = json.loads((manager.save_dir / marker['manifest_file']).read_text())
        db = sqlite3.connect(manager.save_dir / manifest['replacement_file'])
        total, maximum, count = db.execute('SELECT sum(raw_bytes),max(raw_bytes),count(*) FROM records').fetchone()
        db.close()
        manager.close()
        world.event_logger.close()
        print(json.dumps({'route': 'World -> PersistenceManager.publish_root', 'records': count,
            'record_raw_bytes_total': total, 'record_raw_bytes_max': maximum, 'publish_seconds': seconds,
            'rss_before_bytes_macos': before, 'rss_peak_bytes_macos': peak,
            'rss_additional_bytes_macos': peak-before, 'bytes_written': marker['bytes_written'],
            'world_payload_strings_shared': True, 'whole_world_snapshot_forbidden': True}, indent=2))
asyncio.run(main())
