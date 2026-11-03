"""发行工作树复用已安装依赖前，按声明锁与隐藏锁的已有字段核对。"""
import json

def lock_report(declared, installed):
    optional = []
    matched = 0
    for key, expected in declared['packages'].items():
        if not key: continue
        actual = installed['packages'].get(key)
        if actual is None:
            assert expected.get('optional'), 'LOCK_TREE_MISMATCH ' + key + ' missing required dependency'
            optional.append(key)
            continue
        for field in ['version', 'resolved', 'integrity']:
            assert expected.get(field) == actual.get(field), 'LOCK_TREE_MISMATCH ' + key + ' ' + field
        matched += 1
    return {'matchedEntries': matched, 'optionalNotInstalled': sorted(optional)}

def prepare_dependencies(root, shared):
    report = lock_report(json.loads((root / 'package-lock.json').read_text()), json.loads((shared / '.package-lock.json').read_text()))
    (root / 'node_modules').symlink_to(shared.resolve(), target_is_directory=True)
    return report
