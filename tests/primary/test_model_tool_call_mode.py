import pytest

from society0 import LLMModel


pytestmark = pytest.mark.primary


def test_openai_compatible_model_exposes_prompted_json_tool_mode_to_runtime():
    model = LLMModel.openai_compatible(
        model="model-without-native-tools",
        base_url="http://localhost:9999/v1",
        api_key="test",
        tool_call_mode="prompted_json",
    )

    assert model.endpoint_config()["tool_call_mode"] == "prompted_json"


def test_llm_model_rejects_unknown_tool_call_mode():
    with pytest.raises(ValueError, match="tool_call_mode"):
        LLMModel.openai_compatible(
            model="test",
            base_url="http://localhost:9999/v1",
            api_key="test",
            tool_call_mode="unknown",
        )
