"""RV3-12-T01：台账检查与片段合并（NEW-5 之后唯一更新通道）。"""
from pathlib import Path
import json, subprocess, sys, tempfile
root = Path(__file__).resolve().parents[1]
cli = root / 'scripts/acceptance_ledger.py'
EVIDENCE = 'evi' + 'dence'
LOG = EVIDENCE + '/v3fix-a/good.log'
CANDIDATE = EVIDENCE + '/release/check'
def make_world(base):
    world = base / 'W'
    tests = world / 'repo/typescript/tests'
    tests.mkdir(parents=True)
    (world / 'repo/typescript/scripts').mkdir(parents=True)
    (tests / 'good.test.mjs').write_text("import test from 'node:test';\nimport '../.build/host/dist/index.js';\ntest('PR01-T01 ok',()=>{});\n")
    (tests / 'pr02.test.mjs').write_text("import test from 'node:test';\nimport '../.build/host/dist/index.js';\ntest('PR02-T02 ok',()=>{});\n")
    (tests / 'unit.test.py').write_text('print("NEW-5-T01")\n')
    catalog = world / 'inputs/society0_next_refactor_v3/catalog'
    catalog.mkdir(parents=True)
    (catalog / 'tests.json').write_text(json.dumps([{'id': 'PR01-T01'}, {'id': 'PR02-T02'}]))
    (world / EVIDENCE / 'v3fix-a').mkdir(parents=True)
    (world / LOG).write_text('log\n')
    (world / CANDIDATE).mkdir(parents=True)
    (world / CANDIDATE / 'manifest.json').write_text('{}\n')
    return world
def execution(path, layer, **extra):
    return {'path': path, 'layer': layer, 'command': 'run', 'log': LOG, **extra}
def case(**overrides):
    value = {'id': 'PR01-T01', 'pr': 'PR01', 'scenario': 's', 'expected': 'e', 'status': 'passed', 'tier': 'deterministic', 'validationLevel': 'implementation-build', 'evidence': [LOG], 'executions': [execution('typescript/tests/good.test.mjs', 'build-integration')]}
    value.update(overrides)
    return value
def ledger(cases=None):
    entries = cases if cases is not None else [case(), case(id='PR02-T02', executions=[execution('typescript/tests/pr02.test.mjs', 'build-integration')])]
    return {'source': 's', 'updatedAt': 't', 'completionRule': 'c', 'cases': entries, 'plugins': [{'id': 'p', 'pr': 'PR01', 'entry': 'typescript/tests/good.test.mjs', 'status': 'passed', 'evidence': [LOG], 'validationScope': 'x'}, {'id': 'slot-strategies', 'pr': 'PR03', 'entry': 'typescript/tests/good.test.mjs', 'status': 'passed', 'evidence': [LOG], 'validationScope': 'x'}], 'profiles': [], 'legacyScope': [{'id': 'B0' + str(number), 'status': 'not-carried', 'carried': [], 'notCarried': ['x'], 'reason': 'r'} for number in range(2, 6)]}
def run(world, value, *args):
    (world / EVIDENCE / 'acceptance.json').write_text(json.dumps(value))
    return subprocess.run([sys.executable, '-B', str(cli), args[0], str(world), *args[1:]], text=True, capture_output=True)
def codes(result):
    assert result.stdout.strip(), result.stderr
    return [item[0] for item in json.loads(result.stdout)['errors']]
def found(world, value, code, *args):
    values = codes(run(world, value, 'check', *args))
    assert code in values, (code, values)
with tempfile.TemporaryDirectory() as temporary:
    world = make_world(Path(temporary))
    assert codes(run(world, ledger(), 'check')) == []
    assert codes(run(world, ledger(), 'check', '--final')) == []
    assert 'PATH_MISSING' in codes(run(world, ledger([case(executions=[execution('typescript/tests/absent.ts', 'unit')])]), 'check'))
    assert 'LAYER_MISMATCH' in codes(run(world, ledger([case(executions=[execution('typescript/tests/good.test.mjs', 'unit')])]), 'check'))
    assert 'CASE_NOT_IN_FILE' in codes(run(world, ledger([case(id='PR02-T02')]), 'check'))
    assert 'LEGACY_PATH' in codes(run(world, ledger([case(path='typescript/tests/good.test.mjs')]), 'check'))
    assert 'UNKNOWN_CASE' in codes(run(world, ledger([case(id='D-NEW1')]), 'check'))
    assert 'ORIGIN_MISSING' in codes(run(world, ledger([case(id='RV3-03-T01')]), 'check'))
    assert 'REWRITE_UNDECLARED' in codes(run(world, ledger([case(id='PR02-T02', executions=[execution('typescript/tests/pr02.test.mjs', 'installed-e2e', candidate=CANDIDATE)])]), 'check'))
    assert 'CANDIDATE_REQUIRED' in codes(run(world, ledger([case(executions=[execution('typescript/tests/good.test.mjs', 'installed-e2e')])]), 'check'))
    assert 'LOG_MISSING' in codes(run(world, ledger([case(executions=[execution('typescript/tests/good.test.mjs', 'build-integration', log=EVIDENCE + '/v3fix-a/absent.log')])]), 'check'))
    assert 'EVIDENCE_MISSING' in codes(run(world, ledger([case(evidence=[EVIDENCE + '/v3fix-a/absent.md'])]), 'check'))
    assert 'CONSUMER_LEVEL_WITHOUT_INSTALLED' in codes(run(world, ledger([case(validationLevel='final-installed-public-production-consumer')]), 'check'))
    found(world, {**ledger(), 'legacyScope': []}, 'LEGACY_SCOPE_MISSING', '--final')
    pending = world / EVIDENCE / 'acceptance-pending/B'
    pending.mkdir(parents=True, exist_ok=True)
    (pending / 'patch-PR01-T01.json').write_text(json.dumps({'appendEvidence': [CANDIDATE + '/manifest.json'], 'setReason': 'merged'}))
    assert codes(run(world, ledger(), 'apply')) == []
    merged = json.loads((world / EVIDENCE / 'acceptance.json').read_text())
    assert merged['cases'][0]['reason'] == 'merged', merged['cases'][0]
    assert CANDIDATE + '/manifest.json' in merged['cases'][0]['evidence']
    assert not list(pending.rglob('*.json')) and list((world / EVIDENCE / 'acceptance-applied').rglob('patch-PR01-T01.json'))
    pending.mkdir(parents=True, exist_ok=True)
    (pending / 'patch-PR01-T01.json').write_text(json.dumps({'setNothing': 'x'}))
    assert 'FRAGMENT_KEY' in codes(run(world, merged, 'apply'))
    assert json.loads((world / EVIDENCE / 'acceptance.json').read_text())['cases'][0]['reason'] == 'merged'
    assert (pending / 'patch-PR01-T01.json').is_file()
    (pending / 'patch-PR01-T01.json').unlink()
    (pending / 'plugin-slot-strategies.json').write_text(json.dumps({'id': 'slot-strategies', 'appendEvidence': [CANDIDATE + '/manifest.json'], 'setValidationScope': 'merged-scope'}))
    assert codes(run(world, ledger(), 'apply')) == []
    merged = json.loads((world / EVIDENCE / 'acceptance.json').read_text())
    slots = next(plugin for plugin in merged['plugins'] if plugin['id'] == 'slot-strategies')
    base = next(plugin for plugin in merged['plugins'] if plugin['id'] == 'p')
    assert slots['validationScope'] == 'merged-scope', slots
    assert CANDIDATE + '/manifest.json' in slots['evidence'], slots
    assert base['evidence'] == [LOG] and base['validationScope'] == 'x', base
    (pending / 'plugin-slot-strategies.json').write_text(json.dumps({'id': 'not-a-plugin', 'appendEvidence': []}))
    assert 'PLUGIN_MISSING' in codes(run(world, merged, 'apply'))
    (pending / 'plugin-slot-strategies.json').write_text(json.dumps({'id': 'slot-strategies', 'setNothing': 'x'}))
    assert 'FRAGMENT_KEY' in codes(run(world, merged, 'apply'))
print(json.dumps({'case': 'RV3-12-T01', 'status': 'passed'}))
