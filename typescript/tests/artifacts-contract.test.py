"""用真实历史候选验证产物身份准入；不改写候选内容。"""
from pathlib import Path
import os,subprocess,sys,unittest
CHECKER=Path(__file__).with_name('artifacts.test.py')
class ArtifactContract(unittest.TestCase):
    def test_historical_candidate_rejects_expected_release_identity(self):
        result=subprocess.run([sys.executable,'-B',str(CHECKER),os.environ['ARTIFACT_OLD_CANDIDATE'],os.environ['ARTIFACT_EXPECTED_COMMIT']],text=True,capture_output=True)
        self.assertNotEqual(result.returncode,0,'old checker falsely accepts a different or absent clean-release identity')
        self.assertIn('ARTIFACT_IMPLEMENTATION',result.stderr)
if __name__=='__main__':unittest.main()
