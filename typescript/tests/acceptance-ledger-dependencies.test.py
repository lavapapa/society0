"""本地JS依赖图决定真实build层，调用方须传物理路径。"""
from pathlib import Path
import os,sys,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import acceptance_ledger as ledger
class Dependencies(unittest.TestCase):
 def setUp(self):
  self.context=tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']);self.addCleanup(self.context.cleanup);self.world=Path(self.context.name);self.tests=self.world/'repo/typescript/tests';self.tests.mkdir(parents=True)
 def write(self,name,text):
  p=self.tests/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text);return p
 def graph(self):
  entry=self.write('entry.test.mjs',"// PR01-T01\nimport './helper.mjs';\n")
  self.write('helper.mjs',"import value from './nested/other.js';\n")
  self.write('nested/other.js',"await import('../helper.mjs');\nimport '../.build/core/dist/index.js';\n")
  return entry
 def test_transitive_actual_dependency_is_build(self):
  p=self.graph();self.assertEqual(ledger.derived_layers(p,p.read_text()),{'build-integration'})
 def test_cycle_shared_dependency_missing_local_and_bare_imports_remain_unit(self):
  p=self.write('entry.test.mjs',"import './one.mjs';import './two.js';import './absent.mjs';import 'node:test';")
  self.write('one.mjs',"import './two.js';");self.write('two.js',"await import('./one.mjs');")
  self.assertEqual(ledger.derived_layers(p,p.read_text()),{'unit'})
 def test_actual_execution_validation_passes_physical_path(self):
  self.graph();entry={'path':'typescript/tests/entry.test.mjs','layer':'build-integration'}
  self.assertEqual(ledger.execution_errors('PR01-T01',{'status':'unverified'},entry,self.world),[])
 def test_owned_migration_classifies_the_same_dependency(self):
  p=self.graph();texts={p:p.read_text()};self.assertEqual([e['layer'] for e in ledger.migration_executions('PR01-T01',texts,self.tests)],['build-integration'])
 def test_real_registered_pr07_is_both_build_and_installed(self):
  p=ROOT/'tests/pr07-exploration.test.mjs';self.assertEqual(ledger.derived_layers(p,p.read_text()),{'build-integration','installed-e2e'})
 def test_existing_exports_build_exclusion_is_preserved(self):
  p=self.write('exports.test.mjs',"import '../.build/host/dist/index.js';");self.assertEqual(ledger.derived_layers(p,p.read_text()),{'installed-e2e'})
if __name__=='__main__':unittest.main()
