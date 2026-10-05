"""真实旧根状态的一次性基准转换；不提供产品迁移或完整运行恢复。"""
from __future__ import annotations
import argparse
from collections import Counter
import gc
import gzip
import json
from pathlib import Path
import resource
import sys
import tempfile
import time
import uuid


def rss():
    return next(int(line.split()[1])*1024 for line in Path('/proc/self/status').read_text().splitlines() if line.startswith('VmRSS:'))


def deep_size(value, seen):
    size = 0
    types = Counter()
    stack = [value]
    while stack:
        item = stack.pop()
        identity = id(item)
        if identity in seen:
            continue
        seen.add(identity)
        size += sys.getsizeof(item)
        types[type(item).__name__] += 1
        if isinstance(item, dict):
            for key, child in item.items():
                stack.append(key)
                stack.append(child)
        elif isinstance(item, (list, tuple)):
            stack.extend(item)
    return size, dict(types)


def source_state(source):
    from society0.incremental_checkpoint import V4CheckpointStore
    marker = json.loads((source/'checkpoints/v4/complete/step_000000.json').read_text())
    manifest = json.loads((source/marker['manifest_file']).read_text())
    assert manifest['parent_checkpoint_id'] is None
    with gzip.open(source/manifest['replacement_file'], 'rt', encoding='utf-8') as stream:
        payload = json.load(stream)
    state = {}
    for operation in payload['entries']:
        V4CheckpointStore._apply(state, operation)
    del payload
    gc.collect()
    return state, manifest


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--target', type=Path, required=True)
    p.add_argument('--mode', choices=['profile','convert','resolve','restore','query','verify'], required=True)
    args = p.parse_args()
    from society0.incremental_checkpoint import V4CheckpointStore, PersistenceSchema, _WILDCARD
    from society0.persistence import PersistenceManager
    from society0.observation import ObservationReader
    result = {'mode': args.mode, 'source': str(args.source), 'target': str(args.target),
              'scope': 'state-only benchmark; no Thread or Chroma migration'}
    if args.mode in ['profile','convert','verify']:
        state, manifest = source_state(args.source)
        metadata = manifest['root_metadata']
        schemas = [PersistenceSchema.compile(item['schema'], root_path=tuple(
            _WILDCARD if part=='*' else part for part in item['root_path'])) for item in metadata['persistence_schemas']]
        schema = PersistenceSchema.merge(*schemas)
        PersistenceManager._v4_apply_transient_defaults(state, schema)
    gc.collect()
    before = rss()
    peak_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024
    started = time.perf_counter()
    if args.mode == 'profile':
        groups=[];seen=set()
        encoder=json.JSONEncoder(ensure_ascii=False,separators=(',',':'),allow_nan=False)
        for name, value in state['environment']['state'].items():
            values = value.items() if name=='projections' and isinstance(value,dict) else [(None,value)]
            for subname, child in values:
                label='environment/state/'+name+('/'+str(subname) if subname is not None else '')
                rule=schema.resolve(label.split('/'))
                size, counts=deep_size(child,seen)
                byte_count=sum(len(part.encode('utf-8')) for part in encoder.iterencode(child))
                groups.append({'path':label,'items':len(child) if isinstance(child,(dict,list)) else None,
                    'python_bytes':size,'canonical_json_bytes_streamed':byte_count,'types':counts,
                    'persistence_kind':rule.kind.value if rule else None,'granularity':rule.granularity if rule else None})
        for name,value in state.items():
            if name=='environment':continue
            size,counts=deep_size(value,seen)
            groups.append({'path':name,'python_bytes':size,'types':counts})
        result['groups']=sorted(groups,key=lambda row:row['python_bytes'],reverse=True)
        del seen
    elif args.mode == 'convert':
        if args.target.exists():raise FileExistsError(args.target)
        manager=PersistenceManager(str(args.target))
        manager._v4_schema=schema
        metadata=dict(metadata,run_id='benchmark-'+uuid.uuid4().hex)
        store=V4CheckpointStore(args.target)
        marker=store.publish_root(manager._v4_root_entries(state),metadata=metadata,memory_view={})
        result['checkpoint_id']=marker.get('checkpoint_id')
        result['component_bytes']=sum(p.stat().st_size for p in (args.target/'checkpoints/v4/replacements').iterdir())
        manager.close()
    elif args.mode == 'resolve':
        result['checkpoint_id']=V4CheckpointStore(args.target).resolve(0)['checkpoint_id']
    elif args.mode == 'restore':
        restored=V4CheckpointStore(args.target).restore(0)
        result['state_roots']=list(restored)
    elif args.mode == 'verify':
        restored=V4CheckpointStore(args.target).restore(0)
        assert restored==state
        result['all_values_equal']=True
    elif args.mode == 'query':
        with tempfile.TemporaryDirectory(prefix='society0-real-index-',dir='/tmp') as index:
            reader=ObservationReader(args.target,index_dir=index)
            reader.sync()
            result['index_seconds']=time.perf_counter()-started
            page_start=time.perf_counter()
            page=reader.state_page(path=['environment','state','projections'],limit=100,max_bytes=65536)
            result['page_seconds']=time.perf_counter()-page_start
            result['page_total']=page['total']
            result['page_bytes']=page['bytes']
            result['index_files']={p.name:p.stat().st_size for p in Path(index).iterdir() if p.is_file()}
            reader.close()
    elapsed=time.perf_counter()-started
    gc.collect()
    result.update(seconds=elapsed,rss_before=before,rss_retained=rss(),peak_rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
                  peak_before=peak_before)
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
