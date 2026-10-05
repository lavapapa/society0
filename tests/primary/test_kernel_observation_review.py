"""外部观察消费者的独立资源作用域检查。"""
import pytest
from society0.kernel.storage import StageStore
from society0.kernel.observation import ObservationService,ObservationError


def test_review_closed_service_cannot_launch_new_preparation(tmp_path):
    with StageStore.create(tmp_path/'run',[]):pass
    service=ObservationService(tmp_path/'run',cache_dir=tmp_path/'cache')
    service.close()
    try:
        with pytest.raises(ObservationError,match='closed'):
            service.prepare_complete(0)
        assert service._pending is None
    finally:
        service.close()
