from pathlib import Path
import os,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from build_entries import capture_acceptance

class FrozenAssets(unittest.TestCase):
 def temporary(self):
  context=tempfile.TemporaryDirectory(prefix='g20-frozen-',dir=os.environ['TMPDIR'])
  self.assertEqual(Path(context.name).resolve().parent,Path(os.environ['TMPDIR']).resolve())
  self.addCleanup(context.cleanup)
  return Path(context.name)
 def source(self,base,profiles=True):
  root=base/'source'
  for name in ['tests','examples']+(['profiles'] if profiles else []):(root/name).mkdir(parents=True)
  (root/'tests/gate.mjs').write_bytes(b'// frozen gate\n')
  (root/'tests/fixtures').mkdir();(root/'tests/fixtures/value.json').write_bytes(b'{"value":1}\n')
  (root/'examples/run-profile.mjs').write_bytes(b'export const version=1;\n')
  if profiles:(root/'profiles/rule-sqlite.json').write_text('{"label":"雪","actors":1000}\n')
  return root
 def test_all_assets_are_frozen_by_bytes(self):
  base=self.temporary();root=self.source(base)
  names=['tests/gate.mjs','tests/fixtures/value.json','examples/run-profile.mjs','profiles/rule-sqlite.json']
  before={name:(root/name).read_bytes() for name in names};output=base/'frozen'
  capture_acceptance(root,output)
  for name in names:
   self.assertTrue((output/name).is_file(),name+' absent from candidate inputs')
   (root/name).write_bytes(b'changed source after snapshot\n')
   self.assertEqual((output/name).read_bytes(),before[name])
 def test_missing_profiles_rejects_capture(self):
  base=self.temporary();root=self.source(base,profiles=False)
  with self.assertRaises(FileNotFoundError):capture_acceptance(root,base/'frozen')
 def test_source_import_gate_remains_effective(self):
  base=self.temporary();root=self.source(base)
  (root/'tests/bad.mjs').write_text("import '../pack"+"ages/core/src/index.js';\n")
  with self.assertRaisesRegex(AssertionError,'TEST_BASELINE'):capture_acceptance(root,base/'frozen')
  self.assertFalse((base/'frozen').exists())

if __name__=='__main__':unittest.main()
