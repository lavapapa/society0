"""通过真实子进程验证日志及退出码，测试收据与后续门禁分开。"""
from pathlib import Path
import json,os,sys,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from acceptance_execution import run_command,write_test_receipt
class AcceptanceExecution(unittest.TestCase):
 def setUp(self):
  self.context=tempfile.TemporaryDirectory(prefix='g20-execution-',dir=os.environ['TMPDIR']);self.addCleanup(self.context.cleanup);self.output=Path(self.context.name).resolve();self.assertEqual(self.output.parent,Path(os.environ['TMPDIR']).resolve());self.records=[]
 def test_real_complete_streams_and_native_resource_receipt(self):
  payload='real complete stdout-'*500;record=run_command([sys.executable,'-B','-c',"import sys;print("+repr(payload)+");sys.stderr.write('real stderr')"],self.output,self.output,self.records);self.assertEqual(record['exitCode'],0);self.assertEqual((self.output/record['stdout']).read_text(),payload+'\n');self.assertEqual((self.output/record['stderr']).read_text(),'real stderr');self.assertTrue(0<record['cumulativeChildPeakRssKiB']<=524288);self.assertEqual(json.loads((self.output/'commands.json').read_text()),self.records)
 def test_nonzero_is_recorded_and_cannot_make_passed_receipt(self):
  with self.assertRaisesRegex(AssertionError,'ACCEPTANCE_COMMAND_FAILED'):run_command([sys.executable,'-B','-c',"print('actual failed output');raise SystemExit(7)"],self.output,self.output,self.records)
  self.assertEqual(self.records[0]['exitCode'],7);self.assertIn('actual failed output',(self.output/self.records[0]['stdout']).read_text())
  with self.assertRaisesRegex(AssertionError,'ACCEPTANCE_TESTS_INCOMPLETE'):write_test_receipt(self.output,{'scope':'unit-control'},self.records)
  self.assertFalse((self.output/'result.json').exists())
 def test_later_gate_failure_and_rewrite_cannot_change_test_receipt(self):
  run_command([sys.executable,'-B','-c','pass'],self.output,self.output,self.records);write_test_receipt(self.output,{'scope':'unit-control'},self.records);before=(self.output/'result.json').read_bytes()
  with self.assertRaisesRegex(AssertionError,'ACCEPTANCE_COMMAND_FAILED'):run_command([sys.executable,'-B','-c','raise SystemExit(9)'],self.output,self.output,self.records)
  self.assertEqual((self.output/'result.json').read_bytes(),before);self.assertEqual(len(json.loads(before)['commands']),1);self.assertEqual(len(self.records),2)
  with self.assertRaises(FileExistsError):write_test_receipt(self.output,{'scope':'changed'},self.records[:1])
  self.assertEqual((self.output/'result.json').read_bytes(),before)
if __name__=='__main__':unittest.main()
# cases: RV3-12-T01
