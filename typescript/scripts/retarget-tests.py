"""NEW-5：把tests中冻结快照、源码导入与env兜底确定性改写为当前.build；提交后保留以便追溯。"""
from pathlib import Path
import json, re
root = Path(__file__).resolve().parents[1]
tests = root / 'tests'
ENV_RULES = [
    (re.compile(r"process\.env\.PR07_(?:RULE|THREADS|ACTIONS)_ENTRY\s*\?\?\s*"), ''),
    (re.compile(r"resolve\(process\.env\.(?:PR11_STAGE|RANDOM_STAGE)\s*\?\?\s*'\.build'\)"), "fileURLToPath(new URL('../.build',import.meta.url))"),
]
PATH_RULES = [
    (re.compile(r"\.\./\.\./\.\./evidence/pr05/build/"), '../.build/core/dist/'),
    (re.compile(r"\.\./\.\./\.\./evidence/pr05/runtime/host/dist/"), '../.build/host/dist/'),
    (re.compile(r"\.\./\.\./\.\./evidence/pr06/stage/dist/"), '../.build/core/dist/'),
    (re.compile(r"\.\./\.\./\.\./evidence/pr07/stage(?:-increment|-final)?/(core|extras)/dist/"), r'../.build/\1/dist/'),
    (re.compile(r"\.\./\.\./\.\./evidence/pr10/stage/(host|core|extras|social)/dist/"), r'../.build/\1/dist/'),
    (re.compile(r"\.\./\.\./\.\./evidence/pr10/stage/consumer\.mjs"), '../.build/release.json'),
    (re.compile(r"\.\./packages/core/src/execution-fence/state\.ts"), '../.build/core/dist/execution-fence/state.js'),
    (re.compile(r"\.\./packages/sqlite-native/src/index\.mjs"), '../.build/sqlite-native/src/index.mjs'),
    (re.compile(r"from '@society0/schemastery'"), "from '../.build/schemastery/dist/index.js'"),
    (re.compile(r"from '@society0/host/composition-strategies'"), "from '../.build/host/dist/composition-strategies/index.js'"),
]
TMP_HEAD = "import{tmpdir}from'node:os';import{join}from'node:path';"
TIGHT = "import{mkdtempSync}from'node:fs';import{fileURLToPath}from'node:url';"
LOOSE = "import {mkdtempSync}from'node:fs';import{fileURLToPath}from'node:url';"
FILE_RULES = {
    'sqlite-native.test.mjs': [
        ("import {mkdirSync,mkdtempSync} from 'node:fs';\nimport {fileURLToPath} from 'node:url';\n", "import {mkdtempSync} from 'node:fs';\nimport {tmpdir} from 'node:os';\nimport {join} from 'node:path';\n"),
        ("const root=fileURLToPath(new URL('../packages/sqlite-native/build/',import.meta.url));mkdirSync(root,{recursive:true});", "const root=join(tmpdir(),'sqlite-native-');"),
    ],
    'sqlite-native-boundaries.test.mjs': [
        ("import {fileURLToPath} from 'node:url';", "import {tmpdir} from 'node:os';\nimport {join} from 'node:path';"),
        ("const root=fileURLToPath(new URL('../packages/sqlite-native/build/',import.meta.url));const dir=()=>mkdtempSync(root+'boundary-');", "const root=join(tmpdir(),'sqlite-native-');const dir=()=>mkdtempSync(root+'boundary-');"),
    ],
    'sqlite-native-schema.test.mjs': [
        ("import {mkdtempSync}from'node:fs';import{fileURLToPath}from'node:url';", "import {mkdtempSync}from'node:fs';" + TMP_HEAD),
        ("const root=fileURLToPath(new URL('../packages/sqlite-native/build/',import.meta.url));", "const root=join(tmpdir(),'sqlite-native-');"),
    ],
    'sqlite-native-fk-schema.test.mjs': [
        (TIGHT, "import{mkdtempSync}from'node:fs';" + TMP_HEAD),
        ("const home=mkdtempSync(fileURLToPath(new URL('../packages/sqlite-native/build/fkschema-',import.meta.url)))", "const home=mkdtempSync(join(tmpdir(),'sqlite-native-fkschema-'))"),
    ],
    'sqlite-native-global-memory.test.mjs': [
        (TIGHT, "import{mkdtempSync}from'node:fs';" + TMP_HEAD),
        ("const home=mkdtempSync(fileURLToPath(new URL('../packages/sqlite-native/build/global-heap-',import.meta.url)))", "const home=mkdtempSync(join(tmpdir(),'sqlite-native-global-heap-'))"),
    ],
    'sqlite-native-memory.mjs': [
        ("import {fileURLToPath} from 'node:url';", "import {tmpdir} from 'node:os';\nimport {join} from 'node:path';"),
        ("const root=fileURLToPath(new URL('../packages/sqlite-native/build/',import.meta.url));const home=mkdtempSync(root+'memory-');", "const root=join(tmpdir(),'sqlite-native-');const home=mkdtempSync(root+'memory-');"),
    ],
    'pr11-support.mjs': [("import {pathToFileURL} from 'node:url';", "import {pathToFileURL,fileURLToPath} from 'node:url';")],
    'pr11.test.mjs': [("import {pathToFileURL} from 'node:url';", "import {pathToFileURL,fileURLToPath} from 'node:url';")],
    'pr11-random.test.mjs': [("import { pathToFileURL } from 'node:url';", "import { pathToFileURL, fileURLToPath } from 'node:url';")],
    'pr11-random-bootstrap.test.mjs': [("import {pathToFileURL} from 'node:url';", "import {pathToFileURL,fileURLToPath} from 'node:url';")],
    'native/worker-loss-kit/cases/public-worker-fault.mjs': [
        ("import {resolve,join} from 'node:path';", "import {join} from 'node:path';"),
        ("const output=resolve(process.env.SOCIETY0_FAULT_OUTPUT??new URL('../results',import.meta.url).pathname);", "const output=process.env.SOCIETY0_FAULT_OUTPUT;if(!output)throw Error('SOCIETY0_FAULT_OUTPUT is required (see worker-loss-kit/README.md)');"),
    ],
}
def rewrite(name, text):
    count = 0
    for pattern, replacement in [*ENV_RULES, *PATH_RULES]:
        text, hits = pattern.subn(replacement, text)
        count += hits
    for old, new in FILE_RULES.get(name, []):
        assert old in text, f'RETARGET_ANCHOR {name}: {old[:70]}'
        text = text.replace(old, new)
        count += 1
    return text, count
changes = {}
for file in sorted(tests.rglob('*.mjs')):
    original = file.read_text()
    text, count = rewrite(file.relative_to(tests).as_posix(), original)
    if text == original: continue
    assert count, file.name
    file.write_text(text)
    changes[file.relative_to(tests).as_posix()] = {'edits': count, 'changedLines': sum(a != b for a, b in zip(original.splitlines(), text.splitlines()))}
print(json.dumps({'changed': changes, 'files': len(changes)}, indent=1))
