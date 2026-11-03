"""台账检查与片段合并。用法：acceptance_ledger.py check <W> [--final] | apply <W> [--dry-run] | migrate <W>"""
import datetime, json, re, shutil, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from acceptance_tests import BUILD_EXCLUDED, INSTALLED_COPIES, INSTALLED_ONLY, INSTALLED_PY, INSTALLED_SCRIPTS_PREFIX, LIVE
PLAN_ID = re.compile(r'^PR\d{2}-T\d{2}$')
FIX_ID = re.compile(r'^(RV3-\d{2}|NEW-?\d+)-T\d{2}$')
LAYERS = {'unit', 'build-integration', 'installed-e2e', 'live-model'}
LEVELS = {'implementation-build', 'author-production-consumer', 'independent-and-production-consumer', 'final-production-and-independent-consumer', 'external-production-consumer', 'final-installed-public-production-consumer'}
CONSUMER = LEVELS - {'implementation-build', 'author-production-consumer'}
TOP = {'source', 'updatedAt', 'completionRule', 'cases', 'plugins', 'profiles', 'legacyScope'}
TIERS = {'deterministic', 'performance', 'live-endpoint'}
STATUSES = {'passed', 'unverified', 'failed'}
REWRITER = 'typescript/scripts/acceptance.py'
PATCH_KEYS = {'appendEvidence', 'appendExecutions', 'setReason', 'setStatus', 'setValidationLevel'}
PLUGIN_KEYS = {'appendEvidence', 'setValidationScope'}
SCOPES = {'B02', 'B03', 'B04', 'B05'}
def derived_layers(path, text):
    name = Path(path).name
    layers = set()
    if name in LIVE: layers.add('live-model')
    if name in INSTALLED_ONLY | INSTALLED_PY or name.startswith(INSTALLED_SCRIPTS_PREFIX) or name in INSTALLED_COPIES: layers.add('installed-e2e')
    if re.search(r"\.\./\.build(?:/|['\"])", text) and name not in BUILD_EXCLUDED: layers.add('build-integration')
    return layers or {'unit'}
def installed_execution_errors(cid, execution, file, text, W):
    path = execution.get('path', '')
    errors = []
    if file.name in INSTALLED_COPIES and execution.get('rewrittenBy') != REWRITER: errors.append(('REWRITE_UNDECLARED', cid, path))
    if file.name not in INSTALLED_COPIES and '../.build/' in text: errors.append(('INSTALLED_USES_BUILD', cid, path))
    if not execution.get('candidate') or not (W / execution['candidate'] / 'manifest.json').is_file(): errors.append(('CANDIDATE_REQUIRED', cid, path))
    return errors
def execution_errors(cid, case, execution, W):
    layer = execution.get('layer')
    if layer not in LAYERS: return [('LAYER_ENUM', cid, execution)]
    path = execution.get('path', '')
    file = W / 'repo' / path
    if not file.is_file(): return [('PATH_MISSING', cid, path)]
    text = file.read_text()
    layers = derived_layers(path, text)
    errors = [('LAYER_MISMATCH', cid, path, sorted(layers))] if layer not in layers else []
    if cid not in text: errors.append(('CASE_NOT_IN_FILE', cid, path))
    if layer == 'installed-e2e': errors += installed_execution_errors(cid, execution, file, text, W)
    if case.get('status') == 'passed' and not (execution.get('log') and (W / execution['log']).is_file()): errors.append(('LOG_MISSING', cid, execution.get('log')))
    return errors
def case_identity_errors(cid, case, plan_ids):
    errors = []
    if not (PLAN_ID.match(cid or '') and cid in plan_ids) and not FIX_ID.match(cid or ''): errors.append(('UNKNOWN_CASE', cid))
    if 'path' in case: errors.append(('LEGACY_PATH', cid))
    return errors
def case_shape_errors(cid, case):
    errors = []
    for field, allowed, code in [('status', STATUSES, 'STATUS_ENUM'), ('tier', TIERS, 'TIER_ENUM'), ('validationLevel', LEVELS, 'LEVEL_ENUM')]:
        if case.get(field) not in allowed: errors.append((code, cid))
    if FIX_ID.match(cid or '') and not case.get('origin', {}).get('finding'): errors.append(('ORIGIN_MISSING', cid))
    return errors
def passed_case_errors(cid, case, layers):
    errors = []
    if case.get('validationLevel') in CONSUMER and 'installed-e2e' not in layers: errors.append(('CONSUMER_LEVEL_WITHOUT_INSTALLED', cid))
    if case.get('tier') == 'live-endpoint' and 'live-model' not in layers: errors.append(('LIVE_WITHOUT_LIVE_MODEL', cid))
    if case.get('validationLevel') == 'implementation-build' and layers <= {'unit'}: errors.append(('BUILD_LEVEL_UNIT_ONLY', cid))
    return errors
def case_errors(case, W, plan_ids, final):
    cid = case.get('id')
    errors = case_identity_errors(cid, case, plan_ids) + case_shape_errors(cid, case)
    executions = case.get('executions') or []
    if case.get('status') == 'passed' and not executions: errors.append(('NO_EXECUTION', cid))
    for execution in executions: errors += execution_errors(cid, case, execution, W)
    errors += [('EVIDENCE_MISSING', cid, path) for path in case.get('evidence', []) if not (W / path).exists()]
    layers = {execution.get('layer') for execution in executions}
    if case.get('status') == 'passed': errors += passed_case_errors(cid, case, layers)
    return errors
def plugin_errors(plugin, W):
    errors = [('PLUGIN_ENTRY_MISSING', plugin.get('id'))] if not (W / 'repo' / plugin.get('entry', '')).is_file() else []
    return errors + [('EVIDENCE_MISSING', plugin.get('id'), path) for path in plugin.get('evidence', []) if not (W / path).exists()]
def duplicate_errors(identifiers, code):
    return [(code, value) for value in sorted({value for value in identifiers if identifiers.count(value) > 1})]
def ledger_errors(ledger, W, plan_ids, final):
    errors = [('UNKNOWN_TOP_KEY', key) for key in ledger if key not in TOP]
    ids = [case.get('id') for case in ledger.get('cases', [])]
    errors += duplicate_errors(ids, 'DUPLICATE_CASE')
    errors += [('PLAN_CASE_MISSING', cid) for cid in sorted(set(plan_ids) - set(ids))]
    for case in ledger.get('cases', []): errors += case_errors(case, W, plan_ids, final)
    plugins = [plugin.get('id') for plugin in ledger.get('plugins', [])]
    errors += duplicate_errors(plugins, 'DUPLICATE_PLUGIN')
    for plugin in ledger.get('plugins', []): errors += plugin_errors(plugin, W)
    scopes = {scope.get('id') for scope in ledger.get('legacyScope', [])}
    if final and scopes != SCOPES: errors.append(('LEGACY_SCOPE_MISSING', sorted(scopes)))
    pending = W / 'evidence/acceptance-pending'
    if final and pending.is_dir() and any(pending.rglob('*.json')): errors.append(('PENDING_FRAGMENTS', sorted(str(path) for path in pending.rglob('*.json'))[:5]))
    return errors
def fragment_kind(name):
    for kind in ('plugin-new', 'legacyScope', 'plugin', 'patch', 'case'):
        if name.startswith(kind + '-'): return kind
    return name.split('-', 1)[0]
def read_fragments(W):
    pending = W / 'evidence/acceptance-pending'
    return [(path, fragment_kind(path.name), json.loads(path.read_text())) for path in sorted(pending.rglob('*.json'))] if pending.is_dir() else []
def execution_slot(execution):
    return execution.get('path'), execution.get('layer')
def completed_executions(existing, incoming):
    completed = {execution_slot(execution) for execution in incoming if execution.get('log')}
    return [execution for execution in existing if execution.get('log') or execution_slot(execution) not in completed] + incoming

def merge_case(ledger, name, payload):
    kind, ident = name.split('-', 1)
    ident = ident[:-5]
    cases = {case['id']: case for case in ledger['cases']}
    if kind == 'case':
        return [('DUPLICATE_CASE', ident)] if ident in cases else [], {'cases': [*ledger['cases'], payload]}
    if kind == 'patch':
        if ident not in cases: return [('PATCH_TARGET_MISSING', ident)], {}
        unknown = sorted(set(payload) - PATCH_KEYS)
        if unknown: return [('FRAGMENT_KEY', ident, unknown)], {}
        target = cases[ident]
        target['evidence'] = [*target.get('evidence', []), *payload.get('appendEvidence', [])]
        target['executions'] = completed_executions(target.get('executions', []), payload.get('appendExecutions', []))
        if 'setReason' in payload: target['reason'] = payload['setReason']
        if 'setStatus' in payload: target['status'] = payload['setStatus']
        if 'setValidationLevel' in payload: target['validationLevel'] = payload['setValidationLevel']
        return [], {}
    return [], {}
def merge_plugin(ledger, name, payload):
    plugins = {plugin['id']: plugin for plugin in ledger.get('plugins', [])}
    if name == 'plugin':
        ident = payload.get('id')
        if ident is None or ident not in plugins: return [('PLUGIN_MISSING', ident)], {}
        unknown = sorted(set(payload) - PLUGIN_KEYS - {'id'})
        if unknown: return [('FRAGMENT_KEY', ident, unknown)], {}
        plugin = plugins[ident]
        plugin['evidence'] = [*plugin.get('evidence', []), *payload.get('appendEvidence', [])]
        if 'setValidationScope' in payload: plugin['validationScope'] = payload['setValidationScope']
        return [], {}
    if name == 'plugin-new':
        if payload.get('id') in plugins: return [('DUPLICATE_PLUGIN', payload.get('id'))], {}
        return [], {'plugins': [*ledger.get('plugins', []), payload]}
    return [], {}
def merge(ledger, fragments):
    merged = json.loads(json.dumps(ledger))
    errors = []
    for path, kind, payload in fragments:
        if kind in {'case', 'patch'}:
            found, update = merge_case(merged, path.name, payload)
            errors += found
            for key, value in update.items(): merged[key] = value
        elif kind in {'plugin', 'plugin-new'}:
            found, update = merge_plugin(merged, kind, payload)
            errors += found
            for key, value in update.items(): merged[key] = value
        elif kind == 'legacyScope':
            scopes = [scope for scope in merged.get('legacyScope', []) if scope.get('id') != payload.get('id')]
            merged['legacyScope'] = [*scopes, payload]
        else:
            errors.append(('FRAGMENT_KIND', path.name))
    return errors, merged
def read_json(path):
    return json.loads(path.read_text())
def plan_ids(W):
    return [case['id'] for case in read_json(W / 'inputs/society0_next_refactor_v3/catalog/tests.json')]
def report(errors, dry_run=False):
    print(json.dumps({'errors': errors, 'dryRun': dry_run}, ensure_ascii=False, indent=1))
    return 1 if errors else 0
def command(W, kind):
    ledger = read_json(W / 'evidence/acceptance.json')
    ids = plan_ids(W)
    if kind == 'check':
        return report(ledger_errors(ledger, W, ids, '--final' in sys.argv[1:]))
    if kind == 'apply':
        dry_run = '--dry-run' in sys.argv[1:]
        fragments = read_fragments(W)
        errors, merged = merge(ledger, fragments)
        ledger_errors_found = ledger_errors(merged, W, ids, False)
        if dry_run:
            print(json.dumps({'errors': errors, 'ledgerErrors': ledger_errors_found, 'dryRun': True, 'fragments': [path.name for path, kind, payload in fragments]}, ensure_ascii=False, indent=1))
            return 1 if errors else 0
        errors += ledger_errors_found
        if errors: return report(errors, dry_run)
        merged['updatedAt'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        mtime = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        (W / 'evidence/acceptance.json').write_text(json.dumps(merged, ensure_ascii=False, indent=2) + '\n')
        for path, kind, payload in fragments:
            target = W / 'evidence/acceptance-applied' / mtime / path.parent.name
            target.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(target / path.name))
        print(json.dumps({'errors': [], 'applied': [path.name for path, kind, payload in fragments], 'movedTo': f'evidence/acceptance-applied/{mtime}'}, ensure_ascii=False, indent=1))
        return 0
    return migrate(W, ledger)
def migration_execution(path, tests, layer):
    relative = 'typescript/tests/' + path.relative_to(tests).as_posix()
    record = {'path': relative, 'layer': layer, 'command': 'python3 -B tests/' + path.name if layer == 'unit' and path.suffix == '.py' else 'node --test --test-concurrency=1 tests/' + path.name}
    if layer == 'installed-e2e' and path.name in INSTALLED_COPIES: record['rewrittenBy'] = REWRITER
    return record
def migration_executions(cid, texts, tests):
    executions = []
    for path, text in texts.items():
        if cid not in text: continue
        relative = 'typescript/tests/' + path.relative_to(tests).as_posix()
        executions += [migration_execution(path, tests, layer) for layer in sorted(derived_layers(relative, text))]
    return executions
def migrate(W, ledger):
    tests = W / 'repo/typescript/tests'
    texts = {path: path.read_text(errors='ignore') for path in sorted(tests.rglob('*')) if path.is_file() and path.suffix in {'.mjs', '.js', '.ts', '.py'}}
    report_data = {}
    for case in ledger['cases']:
        cid = case['id']
        executions = migration_executions(cid, texts, tests)
        case.pop('path', None)
        report_data[cid] = sorted({execution['path'] for execution in executions})
        if executions: case['executions'] = executions
        elif case.get('status') == 'passed': case['status'] = 'unverified'; case['reason'] = 'no executable test in repository; evidence-only record'
        case.setdefault('validationLevel', 'implementation-build')
    ledger['updatedAt'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    target = W / 'evidence/v3fix-a/ledger/migration.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report_data, ensure_ascii=False, indent=1) + '\n')
    (W / 'evidence/acceptance.json').write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'migrated': len(report_data), 'withExecutions': sum(1 for value in report_data.values() if value), 'report': str(target)}, ensure_ascii=False, indent=1))
    return 0
if __name__ == '__main__':
    if len(sys.argv) < 3 or sys.argv[1] not in {'check', 'apply', 'migrate'}: raise SystemExit('usage: acceptance_ledger.py check|apply|migrate <W> [--final|--dry-run]')
    raise SystemExit(command(Path(sys.argv[2]).resolve(), sys.argv[1]))
