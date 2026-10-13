"""真实 LLM 验证按显式能力请求配置，嵌入配置独立必需。"""
import pytest
from tests.e2e.core_next_real_support import configuration, public_profile


def test_llm_only_configuration_does_not_require_or_install_embedding(monkeypatch,tmp_path):
    for suffix in ('URL','MODEL','KEY'):
        monkeypatch.delenv('SOCIETY0_REAL_EMBED_'+suffix,raising=False)
    for suffix,value in {'RELEASE':'fixed-test-source','OUTPUT':str(tmp_path),'LLM_URL':'http://unused.invalid/v1',
                          'LLM_MODEL':'fixture','LLM_KEY':'test-only-key'}.items():
        monkeypatch.setenv('SOCIETY0_REAL_'+suffix,value)
    actual=configuration(embedding=False)
    assert 'embed' not in actual
    assert list(public_profile(actual))==['llm']
    assert 'test-only-key' not in repr(actual)
    assert 'api_key' not in public_profile(actual)['llm']['endpoints'][0]
    with pytest.raises(ValueError,match='SOCIETY0_REAL_EMBED_URL'):
        configuration()
