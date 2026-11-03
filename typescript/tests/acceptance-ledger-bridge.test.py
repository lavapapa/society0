"""自有W中执行真实Node及真实ledger apply/check；真实项目台账不变。"""
from pathlib import Path
import json,os,shutil,subprocess,sys,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from acceptance_ledger_bridge import preflight,write_installed_fragments,pending_owner
from acceptance_execution import run_command,write_test_receipt
CID='PR01-T01';SOURCE='typescript/tests/consumer.test.mjs'
class LedgerBridge(unittest.TestCase):
 def setUp(self):
  self.context=tempfile.TemporaryDirectory(prefix='g20-bridge-',dir=os.environ['TMPDIR']);self.addCleanup(self.context.cleanup);self.world=Path(self.context.name).resolve();self.assertEqual(self.world.parent,Path(os.environ['TMPDIR']).resolve());tests=self.world/'repo/typescript/tests';tests.mkdir(parents=True);self.text="import test from 'node:test';test('owned control receipt',()=>{}); // PR01-T01\n";(tests/'consumer.test.mjs').write_text(self.text);catalog=self.world/'inputs/society0_next_refactor_v3/catalog';catalog.mkdir(parents=True);(catalog/'tests.json').write_text(json.dumps([{'id':CID}]));self.records=[];self.case={'id':CID,'status':'passed','tier':'deterministic','validationLevel':'final-installed-public-production-consumer','executions':[{'path':SOURCE,'layer':'installed-e2e'}]};self.ledger={'cases':[self.case],'plugins':[],'profiles':[],'legacyScope':[{'id':x} for x in ['B02','B03','B04','B05']]};self.EVIDENCE=self.world/('evi'+'dence');self.EVIDENCE.mkdir();self.save();self.before=(self.EVIDENCE/'acceptance.json').read_bytes();self.output=self.EVIDENCE/'final-consumer';self.candidate=self.EVIDENCE/'release/owned';self.candidate.mkdir(parents=True);(self.candidate/'manifest.json').write_text('{}');self.targets={SOURCE:'consumer.test.mjs'}
 def save(self):(self.EVIDENCE/'acceptance.json').write_text(json.dumps(self.ledger))
 def execute_tests(self,fail=False):
  consumer=self.output/'consumer';consumer.mkdir(parents=True);inputs=self.output/'inputs/tests';inputs.mkdir(parents=True);(inputs/'consumer.test.mjs').write_text(self.text);(consumer/'consumer.test.mjs').write_text(self.text if not fail else "import test from 'node:test';test('actual failure',()=>{throw Error('owned failure')});")
  return run_command(['node','--test','--test-concurrency=1','consumer.test.mjs'],consumer,self.output,self.records)
 def receipt(self):write_test_receipt(self.output,{'unitControl':True,'mode':'candidate','candidate':str(self.candidate)},self.records)
 def test_only_final_installed_templates_are_deferred(self):
  ready=preflight(self.world,self.candidate,self.output,set(self.targets));self.assertEqual(len(ready['remainingErrors']),2);self.assertEqual((self.EVIDENCE/'acceptance.json').read_bytes(),self.before);self.assertFalse(self.output.exists());self.assertFalse(pending_owner(self.world,self.output).exists())
 def test_other_unlogged_layer_even_same_case_blocks(self):
  path=self.world/'repo/typescript/tests/local.test.py';path.write_text('# PR01-T01\n');self.case['executions'].append({'path':'typescript/tests/local.test.py','layer':'unit'});self.save()
  with self.assertRaisesRegex(AssertionError,'ACCEPTANCE_LEDGER_NOT_READY'):preflight(self.world,self.candidate,self.output,set(self.targets))
  self.assertFalse(self.output.exists());self.assertFalse(pending_owner(self.world,self.output).exists())
 def test_real_receipt_applies_templates_and_final_gate(self):
  ready=preflight(self.world,self.candidate,self.output,set(self.targets));record=self.execute_tests();self.receipt();before=(self.output/'result.json').read_bytes();written=write_installed_fragments(self.world,self.candidate,self.output,ready['ledger'],record,self.targets);self.assertEqual(written,[CID]);payload=json.loads((pending_owner(self.world,self.output)/('patch-'+CID+'.json')).read_text());self.assertEqual(payload['appendExecutions'][0]['layer'],'installed-e2e')
  for args in [['apply',str(self.world)],['check',str(self.world),'--final']]:
   result=subprocess.run([sys.executable,'-B',str(ROOT/'scripts/acceptance_ledger.py'),*args],capture_output=True,text=True);self.assertEqual(result.returncode,0,result.stdout+result.stderr)
  actual=json.loads((self.EVIDENCE/'acceptance.json').read_text());self.assertEqual(len(actual['cases'][0]['executions']),1);self.assertTrue(actual['cases'][0]['executions'][0]['log']);self.assertEqual((self.output/'result.json').read_bytes(),before)
 def test_unexecuted_target_has_no_pending_side_effect(self):
  ready=preflight(self.world,self.candidate,self.output,set(self.targets));record=self.execute_tests();self.receipt()
  with self.assertRaisesRegex(AssertionError,'ACCEPTANCE_TEST_NOT_EXECUTED'):write_installed_fragments(self.world,self.candidate,self.output,ready['ledger'],record,{SOURCE:'never-executed.mjs'})
  self.assertFalse(pending_owner(self.world,self.output).exists())
 def test_failed_node_logs_alone_cannot_generate_executions(self):
  ready=preflight(self.world,self.candidate,self.output,set(self.targets))
  with self.assertRaisesRegex(AssertionError,'ACCEPTANCE_COMMAND_FAILED'):self.execute_tests(True)
  with self.assertRaisesRegex(AssertionError,'ACCEPTANCE_RECEIPT_REQUIRED'):write_installed_fragments(self.world,self.candidate,self.output,ready['ledger'],self.records[-1],self.targets)
  self.assertFalse(pending_owner(self.world,self.output).exists())
 def test_receipt_for_other_candidate_is_rejected(self):
  ready=preflight(self.world,self.candidate,self.output,set(self.targets));record=self.execute_tests();self.receipt();other=self.EVIDENCE/'release/other'
  with self.assertRaisesRegex(AssertionError,'ACCEPTANCE_RECEIPT_CANDIDATE'):write_installed_fragments(self.world,other,self.output,ready['ledger'],record,self.targets)
  self.assertFalse(pending_owner(self.world,self.output).exists())
 def test_unplanned_installed_slot_blocks(self):
  with self.assertRaisesRegex(AssertionError,'ACCEPTANCE_LEDGER_NOT_READY'):preflight(self.world,self.candidate,self.output,set())
  self.assertFalse(self.output.exists())
 def test_other_missing_evidence_blocks(self):
  self.case['evidence']=['missing-real-proof.log'];self.save()
  with self.assertRaisesRegex(AssertionError,'ACCEPTANCE_LEDGER_NOT_READY'):preflight(self.world,self.candidate,self.output,set(self.targets))
  self.assertFalse(self.output.exists())
if __name__=='__main__':unittest.main()
# cases: RV3-12-T01
