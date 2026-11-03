"""对照 catalog/plan.json 与运行时 Config，登记每项差异。用法：catalog-reconcile.py <plan.json> [--skeleton]"""
import json, re, sys
from pathlib import Path
REGISTRY = 'docs/config-reconciliation.json'
PLAN = 'inputs/society0_next_refactor_v3/catalog/plan.json'
def read(path):
    return json.loads(path.read_text())
def object_keys(schema):
    if not schema: return []
    node = schema['refs'].get(str(schema['uid']))
    return list((node.get('dict') or {}).keys()) if node and node.get('type') == 'object' else []
def runtime_plugins(build):
    plugins = {}
    for meta_path in sorted(build.glob('*/generated/plugin.meta.json')):
        package = read(meta_path)['package']['name']
        schemas = {entry['exportPath']: entry['schema'] for entry in read(meta_path.with_name('plugin.schema.json'))['schema']['entries']}
        for entry in read(meta_path)['entries']:
            export = entry['exportPath']
            identity = package if export == '.' else package + '/' + export[2:]
            plugins[identity] = {'fields': object_keys(schemas.get(export)), 'policy': [item['path'].split('/')[1] for item in entry['metadata']['configPolicy']]}
    return plugins
def consumer_text(repo, entry):
    path, _, line = str(entry.get('consumer', '')).rpartition(':')
    file = repo / path
    if not line.isdigit() or int(line) < 1 or not file.is_file(): return None
    lines = file.read_text().splitlines()
    index = int(line) - 1
    return lines[index] if index < len(lines) else None
def consumer_line(repo, entry, field):
    text = consumer_text(repo, entry)
    if text is None: return False
    if re.match(r'\s*(?:export\s+)?(?:interface|type|declare|readonly)\b', text): return False
    match = re.search(r'(?:^|[\s{;(])(?:readonly\s+)?' + re.escape(field) + r'\??\s*:(.*)$', text)
    if match and '.' not in match.group(1) and re.fullmatch(r"[\s\w<>\[\]|&?'.;]*", match.group(1)): return False
    return field in text and 'Schema.' not in text and 'configPolicy' not in text
def reconciled_missing(entry, runtime_fields):
    state = entry.get('state')
    if state == 'renamed': return entry.get('runtime') in runtime_fields
    if state == 'deferred': return bool(entry.get('limitation'))
    return state == 'dropped' and bool(entry.get('reason')) and bool(entry.get('design'))
def missing_errors(repo, plugin, plan_fields, runtime_fields, registry):
    errors, entries = [], registry.get('fields', {})
    for field in sorted(set(plan_fields) - set(runtime_fields)):
        entry = entries.get(plugin + '/' + field) or {}
        if not reconciled_missing(entry, runtime_fields): errors.append(('UNRECONCILED', plugin, field))
    return errors
def added_errors(repo, plugin, plan_fields, runtime_fields, registry):
    errors, entries = [], registry.get('fields', {})
    for field in sorted(set(runtime_fields) - set(plan_fields)):
        entry = entries.get(plugin + '/' + field) or {}
        if entry.get('state') != 'added' or not consumer_line(repo, entry, field): errors.append(('UNRECONCILED', plugin, field))
    return errors
def plugin_errors(plan_plugins, runtime, registry):
    errors, entries = [], registry.get('plugins', {})
    for plugin in sorted(set(runtime) - set(plan_plugins)):
        entry = entries.get(plugin) or {}
        if entry.get('state') != 'added-plugin' or not entry.get('design'): errors.append(('UNRECONCILED', plugin))
    for plugin in sorted(set(plan_plugins) - set(runtime)):
        entry = entries.get(plugin) or {}
        if entry.get('state') != 'removed-plugin' or not entry.get('reason'): errors.append(('UNRECONCILED', plugin))
    return errors
def plan_fields(plugin):
    return [field['name'] for field in plugin.get('config', [])]
def skeleton(plan_plugins, runtime, registry):
    entries, plugins = registry.get('fields', {}), registry.get('plugins', {})
    fields = {}
    for identity in sorted(runtime):
        known = set(plan_fields(plan_plugins[identity])) if identity in plan_plugins else set()
        for field in sorted(set(runtime[identity]['fields']) - known):
            fields[identity + '/' + field] = entries.get(identity + '/' + field, {'state': 'unclassified', 'consumer': 'typescript/packages/<pkg>/src/<file>.ts:<line>'})
        for field in sorted(known - set(runtime[identity]['fields'])):
            fields[identity + '/' + field] = entries.get(identity + '/' + field, {'state': 'unclassified', 'reason': '', 'design': ''})
    for identity in sorted(set(runtime) - set(plan_plugins)):
        plugins[identity] = registry.get('plugins', {}).get(identity, {'state': 'unclassified', 'design': 'docs/design-amendments.md'})
    return {'fields': fields, 'plugins': plugins}
def main():
    plan_path = Path(sys.argv[1]).resolve()
    repo = Path(__file__).resolve().parents[2]
    plan = {plugin.get('package') or plugin['id']: plugin for plugin in read(plan_path)['plugins']}
    runtime = runtime_plugins(repo / 'typescript/.build')
    registry = read(repo / 'typescript' / REGISTRY)
    errors = plugin_errors(plan, runtime, registry)
    for identity in sorted(set(plan) & set(runtime)):
        errors += missing_errors(repo, identity, plan_fields(plan[identity]), runtime[identity]['fields'], registry)
        errors += added_errors(repo, identity, plan_fields(plan[identity]), runtime[identity]['fields'], registry)
    output = {'errors': errors}
    if '--skeleton' in sys.argv[1:]: output['skeleton'] = skeleton(plan, runtime, registry)
    print(json.dumps(output, ensure_ascii=False, indent=1))
    return 0 if not errors or '--skeleton' in sys.argv[1:] else 1
if __name__ == '__main__':
    raise SystemExit(main())
