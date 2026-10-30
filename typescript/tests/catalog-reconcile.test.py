"""RV3-16-T02：plan 字段与运行时 Config 的对账登记。"""
from pathlib import Path
import json, subprocess, sys, tempfile
root = Path(__file__).resolve().parents[1]
cli = root / 'scripts/catalog-reconcile.py'
def object_schema(fields, uid=1):
    refs = {str(2 + index): {'type': 'number', 'meta': {}} for index, field in enumerate(fields)}
    return {'uid': uid, 'refs': {**refs, str(uid): {'type': 'object', 'dict': {field: 2 + index for index, field in enumerate(fields)}}}}
def make_world(base, *, plan_fields=('closeTimeoutMs',), runtime_fields=('closeTimeoutMs',), plugins=('resources',), runtime_plugins=None, registry=None, consumer='const closeTimeoutMs = 1;\nconst inlineBytes = 2;\n'):
    world = base / 'W'
    catalog = world / 'inputs/society0_next_refactor_v3/catalog'
    catalog.mkdir(parents=True)
    plan = [{'id': name, 'package': '@society0/host/' + name, 'config': [{'name': field} for field in plan_fields]} for name in plugins]
    (catalog / 'plan.json').write_text(json.dumps({'plugins': plan}))
    build = world / 'repo/typescript/.build/host/generated'
    build.mkdir(parents=True)
    runtime = runtime_plugins or plugins
    entries = [{'exportPath': './' + name, 'metadata': {'title': name, 'configPolicy': [{'path': '/' + field} for field in runtime_fields]}} for name in runtime]
    (build / 'plugin.meta.json').write_text(json.dumps({'package': {'name': '@society0/host'}, 'entries': entries}))
    (build / 'plugin.schema.json').write_text(json.dumps({'schema': {'dialect': 'schemastery', 'entries': [{'exportPath': './' + name, 'schema': object_schema(runtime_fields)} for name in runtime]}}))
    source = world / 'repo/typescript/packages/host/src'
    source.mkdir(parents=True)
    (source / 'resources.ts').write_text(consumer)
    docs = world / 'repo/typescript/docs'
    docs.mkdir(parents=True)
    (docs / 'config-reconciliation.json').write_text(json.dumps(registry or {'fields': {}, 'plugins': {}}))
    return world
def run(world, *args):
    return subprocess.run([sys.executable, '-B', str(cli), str(world / 'inputs/society0_next_refactor_v3/catalog/plan.json'), *args], text=True, capture_output=True)
def errors(result):
    assert result.stdout.strip(), result.stderr
    return [item[0] for item in json.loads(result.stdout)['errors']]
with tempfile.TemporaryDirectory() as temporary:
    base = Path(temporary)
    assert errors(run(make_world(base / 'ok'))) == []
    found = errors(run(make_world(base / 'extra', runtime_fields=('closeTimeoutMs', 'inlineBytes'))))
    assert 'UNRECONCILED' in found, found
    added = {'fields': {'@society0/host/resources/inlineBytes': {'state': 'added', 'consumer': 'typescript/packages/host/src/resources.ts:2'}}, 'plugins': {}}
    assert errors(run(make_world(base / 'added', runtime_fields=('closeTimeoutMs', 'inlineBytes'), registry=added))) == []
    wrong = {'fields': {'@society0/host/resources/inlineBytes': {'state': 'added', 'consumer': 'typescript/packages/host/src/resources.ts:1'}}, 'plugins': {}}
    assert 'UNRECONCILED' in errors(run(make_world(base / 'wrong', runtime_fields=('closeTimeoutMs', 'inlineBytes'), registry=wrong)))
    dropped = {'fields': {'@society0/host/resources/closeTimeoutMs': {'state': 'dropped', 'reason': 'r', 'design': 'd'}}, 'plugins': {}}
    assert errors(run(make_world(base / 'dropped', runtime_fields=(), registry=dropped, consumer='const inlineBytes = 2;\n'))) == []
    orphan = {'fields': {}, 'plugins': {'@society0/host/memory': {'state': 'added-plugin', 'design': 'docs/design-amendments.md'}}}
    assert errors(run(make_world(base / 'orphan', runtime_plugins=('resources', 'memory'), registry=orphan))) == []
    assert 'UNRECONCILED' in errors(run(make_world(base / 'unregistered', runtime_plugins=('resources', 'memory'))))
    declared = {'fields': {'@society0/host/resources/inlineBytes': {'state': 'added', 'consumer': 'typescript/packages/host/src/resources.ts:3'}}, 'plugins': {}}
    assert 'UNRECONCILED' in errors(run(make_world(base / 'declaration', runtime_fields=('closeTimeoutMs', 'inlineBytes'), registry=declared, consumer='const closeTimeoutMs = 1;\nconst inlineBytes = 2;\ninterface Options { readonly inlineBytes: number; }\n')))
    skeleton = run(make_world(base / 'skeleton', runtime_fields=('closeTimeoutMs', 'inlineBytes')), '--skeleton')
    assert skeleton.returncode == 0, skeleton.stderr
    assert '@society0/host/resources/inlineBytes' in skeleton.stdout
print(json.dumps({'case': 'RV3-16-T02', 'status': 'passed'}))
