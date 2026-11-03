"""048性能运行必须显式给出正安全整数超时，参数失败不得创建输出。"""
from pathlib import Path
import os,subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
class ScaleTimeout(unittest.TestCase):
 def test_invalid_or_missing_timeout_has_no_output(self):
  with tempfile.TemporaryDirectory(prefix='g20-scale-cli-',dir=os.environ['TMPDIR']) as directory:
   base=Path(directory).resolve();self.assertEqual(base.parent,Path(os.environ['TMPDIR']).resolve())
   for i,value in enumerate([None,'0','-1','1.5','nan','9007199254740992']):
    with self.subTest(value=value):
     output=base/('result-'+str(i));args=['node',str(ROOT/'tests/pr12-scale.mjs'),str(base/'consumer'),str(output)]
     if value is not None:args.append('--timeout-ms='+value)
     result=subprocess.run(args,cwd=ROOT,capture_output=True,text=True);self.assertNotEqual(result.returncode,0);self.assertIn('Usage: node pr12-scale.mjs',result.stderr);self.assertFalse(output.exists())
if __name__=='__main__':unittest.main()
# cases: PR12-T03
