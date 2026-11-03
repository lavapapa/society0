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
 def test_existing_pr07_t24_public_scenarios_are_installed(self):
  name='pr07-exploration.test.mjs';text=(ROOT/'tests'/name).read_text()
  self.assertEqual(INSTALLED_COPIES[name],'regex-public');self.assertIn('PR07-T24',text)
  self.assertNotIn('../.build/',rewrite_installed(name,text,INSTALLED_COPIES[name]))
  self.assertEqual(INSTALLED_SUPPORT['pr07-support.mjs'],'regex-public')
 def test_pr11_real_policy_history_is_public_installed(self):
  name='pr11-policy-history.test.mjs';self.assertEqual(INSTALLED_COPIES[name],'regex-public')
  self.assertEqual(INSTALLED_SUPPORT['pr11-support.mjs'],'random-native')
  for name,rules in [(name,INSTALLED_COPIES[name]),('pr11-support.mjs',INSTALLED_SUPPORT['pr11-support.mjs'])]:self.assertNotIn('../.build',rewrite_installed(name,(ROOT/'tests'/name).read_text(),rules))
 def test_pr10_historical_pages_use_public_social_and_dynamic_support(self):
  import json,re
  name='pr10-history-pages.test.mjs';self.assertEqual(INSTALLED_COPIES[name],'regex-public')
  for file,rules in [(name,INSTALLED_COPIES[name]),('pr10-support.mjs',INSTALLED_SUPPORT['pr10-support.mjs'])]:
   text=rewrite_installed(file,(ROOT/'tests'/file).read_text(),rules);self.assertNotIn('../.build',text)
   for package,entry in re.findall(r'@society0/(host|core|social)/([a-z-]+)',text):self.assertIn('./'+entry,json.loads((ROOT/'.build'/package/'package.json').read_text())['exports'])
  self.assertIn("import('@society0/core/' + name)",text);self.assertIn("import('@society0/social/' + name)",text)
 def test_platform_faults_keep_actual_installed_worker_publish_and_cli(self):
  from acceptance_tests import INSTALLED_ONLY
  self.assertIn('cli.test.mjs',INSTALLED_ONLY)
  for name in ['platform-worker-loss.test.mjs','pr05-history-gates.test.mjs']:
   self.assertEqual(INSTALLED_COPIES[name],'regex-public');self.assertIn('PR12-T04',(ROOT/'tests'/name).read_text())
  self.assertIn('PR12-T04',(ROOT/'tests/cli.test.mjs').read_text())
  for name in ['public-fault-support.mjs','platform-worker-fault.mjs','pr05-support.mjs','pr05-crash-child.mjs']:
   self.assertNotIn('../.build',rewrite_installed(name,(ROOT/'tests'/name).read_text(),INSTALLED_SUPPORT[name]))
if __name__=='__main__':unittest.main()
# cases: PR11-T03
