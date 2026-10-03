"""在临时安装 Bashkit 的环境显式执行；主仓无该依赖时跳过。"""
import asyncio
import runpy
from pathlib import Path

import pytest

pytest.importorskip('bashkit')
probe = runpy.run_path(str(Path(__file__).parents[2] / 'benchmarks/core_next_shell.py'))


def test_pipeline_and_workspace_roundtrip():
    assert probe['pipelines']()['workspace_restored']


def test_lazy_head_materializes_once():
    assert probe['lazy'](1024 * 1024)['provider_calls'] == 1


def test_async_scoped_builtin_and_rebinding():
    assert asyncio.run(probe['scoped_builtin']())['async_callback_and_restore']


def test_large_binding_output_exact():
    assert probe['transfer'](1024 * 1024)['full_output_equal']


def test_large_output_signals_truncation_despite_success_exit():
    result = probe['transfer'](8 * 1024 * 1024)
    assert result['stdout_truncated']
    assert result['returned_bytes'] == 1024 * 1024
    assert result['exit_code'] == 0
