"""当前公共 VFS 案例的 shell 复算必须成功并返回完整数值。"""
import pytest

def test_shell_recomputation_accepts_autonomous_correction():
    from tests.e2e.test_core_next_real import assert_shell_recomputation
    failed={'stdout':'{"count":12,"total":528}','exit_code':5}
    wrong={'stdout':'{"count":12,"total":528}','exit_code':0}
    correct={'stdout':'{"count":12,"total":546}','exit_code':0}
    assert_shell_recomputation([failed,wrong,correct])

@pytest.mark.parametrize('outputs',[
    [{'stdout':'','exit_code':5}],
    [{'stdout':'{"count":12,"total":528}','exit_code':0}],
    [{'stdout':'{"count":12,"total":546}','exit_code':5}],
])
def test_shell_recomputation_rejects_failed_or_incorrect_result(outputs):
    from tests.e2e.test_core_next_real import assert_shell_recomputation
    with pytest.raises(AssertionError):assert_shell_recomputation(outputs)
