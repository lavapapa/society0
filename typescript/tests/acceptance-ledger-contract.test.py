"""表征台账身份、验收级别、合并和自有微型迁移的现行合同。"""
from pathlib import Path
import copy,json,os,subprocess,sys,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import acceptance_ledger as ledger
EVIDENCE='evi'+'dence'
CID='PR01-T01'
def case(**updates):
 value={'id':CID,'pr':'PR01','status':'unverified','tier':'deterministic','validationLevel':'author-production-consumer','executions':[],'evidence':[]}
 value.update(updates);return value
class LedgerContract(unittest.TestCase):
 def setUp(self):
  self.context=tempfile.TemporaryDirectory(prefix='g20-ledger-contract-',dir=os.environ['TMPDIR']);self.addCleanup(self.context.cleanup);self.world=Path(self.context.name).resolve();self.assertEqual(self.world.parent,Path(os.environ['TMPDIR']).resolve())
  self.tests=self.world/'repo/typescript/tests';self.tests.mkdir(parents=True);self.records=self.world/EVIDENCE;self.records.mkdir();(self.records/'ok.log').write_text('owned metadata fixture\n');(self.tests/'unit.py').write_text('# '+CID+'\n')
 def codes(self,value):return [item[0] for item in value]
 def test_case_shape_and_unverified_limits(self):
  self.assertEqual(ledger.case_errors(case(),self.world,[CID],True),[])
  value=case(id=None,path='old',status='bad',tier='bad',validationLevel='bad')
  self.assertEqual(self.codes(ledger.case_errors(value,self.world,[CID],False)),['UNKNOWN_CASE','LEGACY_PATH','STATUS_ENUM','TIER_ENUM','LEVEL_ENUM'])
  self.assertEqual(self.codes(ledger.case_errors(case(id='RV3-12-T01'),self.world,[CID],False)),['ORIGIN_MISSING'])
 def test_passed_cases_require_real_execution_layers(self):
  self.assertEqual(self.codes(ledger.case_errors(case(status='passed'),self.world,[CID],False)),['NO_EXECUTION'])
  self.assertEqual(self.codes(ledger.case_errors(case(status='passed',tier='live-endpoint'),self.world,[CID],False)),['NO_EXECUTION','LIVE_WITHOUT_LIVE_MODEL'])
  entry={'path':'typescript/tests/unit.py','layer':'unit','log':EVIDENCE+'/ok.log'}
  self.assertEqual(self.codes(ledger.case_errors(case(status='passed',validationLevel='implementation-build',executions=[entry]),self.world,[CID],False)),['BUILD_LEVEL_UNIT_ONLY'])
  live=next(iter(ledger.LIVE));(self.tests/live).write_text('// '+CID+'\n');entry={**entry,'path':'typescript/tests/'+live,'layer':'live-model'}
  self.assertEqual(ledger.case_errors(case(status='passed',tier='live-endpoint',executions=[entry]),self.world,[CID],False),[])
 def test_execution_early_rejections(self):
  self.assertEqual(self.codes(ledger.execution_errors(CID,case(),{'layer':'unknown'},self.world)),['LAYER_ENUM'])
  self.assertEqual(self.codes(ledger.execution_errors(CID,case(),{'layer':'unit','path':'typescript/tests/absent.py'},self.world)),['PATH_MISSING'])
 def test_case_fragments_and_scope_replacement(self):
  original={'cases':[case()],'legacyScope':[{'id':'B02','reason':'old'},{'id':'B03','reason':'kept'}]};before=copy.deepcopy(original)
  added=case(id='PR01-T02');errors,result=ledger.merge(original,[(Path('case-PR01-T02.json'),'case',added)])
  self.assertEqual(errors,[]);self.assertEqual(result['cases'],[case(),added]);self.assertEqual(original,before)
  errors,_=ledger.merge(original,[(Path('case-PR01-T01.json'),'case',case())]);self.assertEqual(self.codes(errors),['DUPLICATE_CASE'])
  errors,_=ledger.merge(original,[(Path('patch-PR01-T09.json'),'patch',{})]);self.assertEqual(self.codes(errors),['PATCH_TARGET_MISSING'])
  patch={'setValidationLevel':'implementation-build','setReason':'characterized','setStatus':'failed'}
  errors,result=ledger.merge(original,[(Path('patch-PR01-T01.json'),'patch',patch)]);self.assertEqual(errors,[]);self.assertEqual(result['cases'][0]['validationLevel'],'implementation-build')
  errors,result=ledger.merge(original,[(Path('legacyScope-B02.json'),'legacyScope',{'id':'B02','reason':'new'})]);self.assertEqual(errors,[]);self.assertEqual(result['legacyScope'],[{'id':'B03','reason':'kept'},{'id':'B02','reason':'new'}])
  errors,_=ledger.merge(original,[(Path('unknown-case.json'),'unknown',{})]);self.assertEqual(self.codes(errors),['FRAGMENT_KIND']);self.assertEqual(ledger.fragment_kind('unknown-case.json'),'unknown')
 def test_identity_sets_and_pending_final_gate(self):
  plugin={'id':'p','entry':'typescript/tests/unit.py','evidence':[]};value={'cases':[case(),case()],'plugins':[plugin,plugin],'unexpected':True}
  codes=self.codes(ledger.ledger_errors(value,self.world,[CID,'PR01-T02'],False));self.assertEqual(codes,['UNKNOWN_TOP_KEY','DUPLICATE_CASE','PLAN_CASE_MISSING','DUPLICATE_PLUGIN'])
  pending=self.records/'acceptance-pending/E';pending.mkdir(parents=True);(pending/'patch-PR01-T01.json').write_text('{}')
  value={'cases':[case()],'plugins':[],'legacyScope':[{'id':key} for key in sorted(ledger.SCOPES)]};self.assertEqual(self.codes(ledger.ledger_errors(value,self.world,[CID],True)),['PENDING_FRAGMENTS'])
 def cli_fixture(self,value,ids):
  file=self.records/'acceptance.json';file.write_text(json.dumps(value));catalog=self.world/'inputs/society0_next_refactor_v3/catalog';catalog.mkdir(parents=True);(catalog/'tests.json').write_text(json.dumps([{'id':cid} for cid in ids]));return file
 def run_cli(self,*args):return subprocess.run([sys.executable,'-B',str(ROOT/'scripts/acceptance_ledger.py'),args[0],str(self.world),*args[1:]],capture_output=True,text=True)
 def test_dry_run_does_not_apply(self):
  file=self.cli_fixture({'cases':[case()],'plugins':[]},[CID]);before=file.read_bytes();pending=self.records/'acceptance-pending/E';pending.mkdir(parents=True);fragment=pending/'patch-PR01-T01.json';fragment.write_text(json.dumps({'setReason':'planned'}))
  result=self.run_cli('apply','--dry-run');self.assertEqual(result.returncode,0,result.stdout+result.stderr);report=json.loads(result.stdout);self.assertEqual(report['errors'],[]);self.assertEqual(report['ledgerErrors'],[]);self.assertTrue(report['dryRun']);self.assertEqual(file.read_bytes(),before);self.assertTrue(fragment.is_file())
 def test_migration_only_in_owned_tiny_world(self):
  build_reference='../.bu'+'ild/host/dist/index.js';(self.tests/'pr02.test.mjs').write_text("// PR02-T02\nimport '"+build_reference+"';\n");(self.tests/'ignored.txt').write_text('PR01-T03')
  first=case(status='passed',path='old');first.pop('validationLevel');value={'cases':[first,case(id='PR02-T02',status='passed'),case(id='PR01-T03',status='passed'),case(id='PR01-T04',reason='pending')],'plugins':[]}
  file=self.cli_fixture(value,[CID,'PR02-T02','PR01-T03','PR01-T04']);result=self.run_cli('migrate');self.assertEqual(result.returncode,0,result.stdout+result.stderr);report=json.loads(result.stdout);self.assertEqual((report['migrated'],report['withExecutions']),(4,2));entries=json.loads(file.read_text())['cases']
  self.assertNotIn('path',entries[0]);self.assertEqual(entries[0]['validationLevel'],'implementation-build');self.assertEqual(entries[0]['executions'][0]['command'],'python3 -B tests/unit.py')
  installed=next(item for item in entries[1]['executions'] if item['layer']=='installed-e2e');self.assertEqual(installed['rewrittenBy'],ledger.REWRITER);self.assertNotIn('log',installed);self.assertNotIn('candidate',installed)
  self.assertEqual(entries[2]['status'],'unverified');self.assertEqual(entries[3]['reason'],'pending');self.assertTrue((self.records/'v3fix-a/ledger/migration.json').is_file())
if __name__=='__main__':unittest.main()
# cases: RV3-12-T01
