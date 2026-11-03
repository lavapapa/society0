from pathlib import Path
import json,sys,tempfile,subprocess,unittest,copy
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import acceptance_ledger as ledger
EVIDENCE='evi'+'dence/'
PENDING={'path':'typescript/tests/consumer.test.mjs','layer':'installed-e2e','command':'node --test tests/consumer.test.mjs'}
ACTUAL={**PENDING,'command':'python3 -B scripts/acceptance.py out --candidate candidate','log':EVIDENCE+'new.log','candidate':EVIDENCE+'release/probe','rewrittenBy':'typescript/scripts/acceptance.py'}
def case(executions):
 return {'id':'PR01-T01','pr':'PR01','status':'unverified','tier':'deterministic','validationLevel':'author-production-consumer','scenario':'ledger fixture','expected':'completion','evidence':[],'executions':executions}
def merge(executions,additions):
 original={'cases':[case(copy.deepcopy(executions))]};before=copy.deepcopy(original)
 errors,result=ledger.merge(original,[(Path('patch-PR01-T01.json'),'patch',{'appendExecutions':copy.deepcopy(additions)})])
 assert errors==[] and original==before
 return result['cases'][0]['executions']
class Completion(unittest.TestCase):
 def test_complete_migration_placeholder(self):
  self.assertEqual(merge([PENDING],[ACTUAL]),[ACTUAL])
 def test_keep_logged_history(self):
  old={**ACTUAL,'log':EVIDENCE+'old.log'}
  self.assertEqual(merge([old],[ACTUAL]),[old,ACTUAL])
 def test_keep_other_execution_slots(self):
  other={**PENDING,'path':'typescript/tests/other.test.mjs'};unit={**PENDING,'layer':'unit'}
  result=merge([PENDING,other,unit],[ACTUAL]);self.assertEqual(len(result),3)
  for expected in [other,unit,ACTUAL]:self.assertIn(expected,result)
 def test_unlogged_addition_keeps_placeholder(self):
  self.assertEqual(merge([PENDING],[PENDING]),[PENDING,PENDING])
 def test_input_is_unchanged(self):
  value=copy.deepcopy(PENDING);merge([value],[ACTUAL]);self.assertEqual(value,PENDING)
 def exercise_cli(self,valid):
  with tempfile.TemporaryDirectory(prefix='g20-ledger-') as directory:
   W=Path(directory).resolve();self.assertEqual(W.parent,Path(tempfile.gettempdir()).resolve())
   test=W/'repo'/PENDING['path'];test.parent.mkdir(parents=True);test.write_text('// PR01-T01 unit fixture for ledger classification\n')
   plan=W/'inputs/society0_next_refactor_v3/catalog/tests.json';plan.parent.mkdir(parents=True);plan.write_text(json.dumps([{'id':'PR01-T01'}]))
   records=W/EVIDENCE;records.mkdir();(records/'new.log').write_text('unit fixture for metadata merge, not a real candidate execution\n')
   current=records/'acceptance.json';current.write_text(json.dumps({'cases':[case([PENDING])],'plugins':[]}));before=current.read_bytes()
   pending=records/'acceptance-pending/E';pending.mkdir(parents=True);fragment=pending/'patch-PR01-T01.json';fragment.write_text(json.dumps({'appendExecutions':[ACTUAL],'setStatus':'passed'}))
   if valid:
    manifest=W/ACTUAL['candidate']/'manifest.json';manifest.parent.mkdir(parents=True);manifest.write_text('{}')
   result=subprocess.run([sys.executable,'-B',str(ROOT/'scripts/acceptance_ledger.py'),'apply',str(W)],capture_output=True,text=True)
   if valid:
    self.assertEqual(result.returncode,0,result.stdout+result.stderr)
    self.assertEqual(json.loads(current.read_text())['cases'][0]['executions'],[ACTUAL]);self.assertFalse(fragment.exists())
   else:
    self.assertEqual(result.returncode,1,result.stdout+result.stderr);self.assertIn('CANDIDATE_REQUIRED',result.stdout)
    self.assertEqual(current.read_bytes(),before);self.assertTrue(fragment.exists())
 def test_actual_apply_completes_and_archives(self):self.exercise_cli(True)
 def test_actual_apply_rejects_missing_manifest_without_write(self):self.exercise_cli(False)
if __name__=='__main__':unittest.main()

# cases: RV3-12-T01
