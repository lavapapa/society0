from pathlib import Path
import json,sys,unittest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from acceptance_copy import rewrite_installed
from acceptance_ledger import derived_layers
from acceptance_tests import INSTALLED_COPIES,INSTALLED_SUPPORT
class InstalledCopy(unittest.TestCase):
 def test_support_uses_existing_public_exports(self):
  name='pr07-support.mjs';text=rewrite_installed(name,(ROOT/'tests'/name).read_text(),INSTALLED_SUPPORT[name]);self.assertNotIn('../.build/',text)
  import re
  for package,suffix in re.findall(r"from ['\"]@society0/(host|core|extras)([^'\"]*)['\"]",text):
   exports=json.loads((ROOT/'packages'/package/'package.json').read_text())['exports'];self.assertIn('.'+suffix,exports)
  self.assertIn('@society0/extras/workspace',text)
 def test_dictionary_requires_actual_source(self):
  self.assertEqual(rewrite_installed('owned','original',{'original':'public'}),'public')
  with self.assertRaises(AssertionError):rewrite_installed('owned','missing',{'original':'public'})
 def test_random_rules_and_registry(self):
  name='pr11-random-order.test.mjs';self.assertEqual(INSTALLED_COPIES[name],'random-native');text=rewrite_installed(name,(ROOT/'tests'/name).read_text(),INSTALLED_COPIES[name]);self.assertNotIn('../.build',text);self.assertIn("const stage=resolve('.')",text)
 def test_directory_url_is_build_execution_and_installed_copy(self):
  name='pr11-random-order.test.mjs';text=(ROOT/'tests'/name).read_text();self.assertEqual(derived_layers('typescript/tests/'+name,text),{'installed-e2e','build-integration'})
 def test_pr08_public_scenarios_are_registered_for_real_installed_execution(self):
  for name in ['pr08-public-display-error.test.mjs','pr08-slow-persistence.test.mjs','pr08-recorded-result.test.mjs']:
   self.assertEqual(INSTALLED_COPIES[name],'regex-public');text=rewrite_installed(name,(ROOT/'tests'/name).read_text(),INSTALLED_COPIES[name]);self.assertNotIn('../.build/',text)
if __name__=='__main__':unittest.main()
# cases: PR11-T03
