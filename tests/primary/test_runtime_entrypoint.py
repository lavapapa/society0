"""主运行入口与事务记录共同能力。"""
import importlib.util

import pytest


@pytest.mark.parametrize('module', ['sim_engine', 'streaming', 'diff_dispatcher', 'event_logger', 'legacy'])
def test_removed_runtime_modules_have_no_importable_entrypoint(module):
    assert importlib.util.find_spec('society0.' + module) is None


def test_current_runtime_keeps_transaction_logger():
    from society0 import CodeSchedule, Society0
    from society0.transaction import EventLogger, TransactionManager
    assert all((CodeSchedule, Society0, EventLogger, TransactionManager))
