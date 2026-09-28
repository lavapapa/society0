"""存储作者对查询 direct scope 的独立语义检查。"""
import json
import random
from society0.incremental_checkpoint import V4CheckpointStore, SealedTickDelta
from society0.observation import ObservationReader


def test_direct_scope_recreate_same_epoch_and_historical_preparation(tmp_path):
    store = V4CheckpointStore(tmp_path)
    state = {}
    history = []
    rng = random.Random(43)
    def key(path): return json.dumps(path, ensure_ascii=False)
    def apply(operations):
        for op in operations:
            path = op['path']
            for identity, (prior, _) in list(state.items()):
                if len(prior) >= len(path) and key(prior[:len(path)]) == key(path):
                    del state[identity]
            if op['operation'] != 'delete': state[key(path)] = (path, op['value'])
    with ObservationReader(tmp_path) as reader:
        for epoch in range(8):
            operations = []
            for _ in range(25):
                branch = rng.choice([True, 1, '1', '重复😀' * 40])
                path = ['scope', branch]
                if rng.random() < .35:
                    operations.extend([{'path':path,'operation':'delete'}, {'path':path,'operation':'set','value':{}}])
                operations.append({'path':path+[str(rng.randrange(6))], 'operation':'set','value':rng.randrange(1000)})
            for i, op in enumerate(operations): op['sequence'] = i
            marker = store.publish_root(operations) if epoch == 0 else store.publish(SealedTickDelta(epoch,tuple(operations),()))
            apply(operations); history.append((marker['checkpoint_id'], dict(state)))
            reader.sync()
            for checkpoint, expected in [history[0], history[-1]]:
                for scope in [[], ['scope'], ['scope', True], ['scope',1], ['scope','1']]:
                    reader.prepare_state(checkpoint, scope)
                    page = reader.state_page(checkpoint, scope, limit=100, max_bytes=100000)
                    wanted = {identity:value for identity,(path,value) in expected.items() if key(path[:len(scope)])==key(scope)}
                    assert page['total'] == len(wanted)
                    assert {key(item['path']):item['value'] for item in page['items']} == wanted
                    reader.clear_prepared_state()
                    direct = reader.state_page(checkpoint, scope, limit=100, max_bytes=100000)
                    assert direct['total'] == len(wanted)
                    assert {key(item['path']):item['value'] for item in direct['items']} == wanted
