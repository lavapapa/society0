"""组件读取保留 JSON 类型与既有错误处理。"""
import pytest

import society0.incremental_checkpoint as checkpoint


def test_restore_preserves_nested_values(tmp_path):
    store = checkpoint.V4CheckpointStore(tmp_path)
    value = {"large": 10 ** 300, "float": 0.125, "nested": [None, True, {"中文": []}]}
    store.publish_root([{"path": ["value"], "operation": "set", "value": value,
                         "sequence": 0}])

    restored = store.restore(0)["value"]
    assert restored == value
    assert type(restored["large"]) is int
    assert type(restored["float"]) is float


def test_invalid_gzip_keeps_component_error(tmp_path):
    store = checkpoint.V4CheckpointStore(tmp_path)
    path = tmp_path / "broken.gz"
    raw = b"not gzip"
    path.write_bytes(raw)
    # 使用既有完整性校验协议构造可抵达解压分支的组件。
    with pytest.raises(ValueError, match="gzip is invalid"):
        store._read_json_component("broken.gz", store._sha256(raw), "replacement")
