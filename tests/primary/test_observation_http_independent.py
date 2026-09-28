"""非查询作者复验：并发边界和请求连接的只读行为。"""
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

from society0.observation import ObservationReader


def test_http_saturation_returns_busy_and_disconnect_releases_slots(tmp_path):
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    process = subprocess.Popen([sys.executable, '-m', 'society0.observation', str(tmp_path),
        '--index-dir', str(tmp_path / 'index'), '--serve', str(port)], stderr=subprocess.DEVNULL,
        env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[2] / 'src')})
    slow = []
    def status():
        request = urllib.request.Request(f'http://127.0.0.1:{port}', data=b'{"method":"status"}')
        try:
            response = urllib.request.urlopen(request, timeout=.6)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)
    try:
        deadline = time.monotonic() + 5
        while True:
            try:
                assert status()[0] == 200
                break
            except OSError:
                assert time.monotonic() < deadline
                time.sleep(.02)
        for _ in range(4):
            client = socket.create_connection(('127.0.0.1', port), timeout=.6)
            client.sendall(b'POST / HTTP/1.0\r\nContent-Length: 100\r\n\r\n{')
            slow.append(client)
        deadline = time.monotonic() + 2
        while True:
            code, result = status()
            if code == 503:
                assert result == {'error': 'server_busy'}
                break
            assert time.monotonic() < deadline
            time.sleep(.02)
        for client in slow:
            client.close()
        slow.clear()
        deadline = time.monotonic() + 2
        while True:
            code, result = status()
            if code == 200:
                assert 'result' in result
                break
            assert time.monotonic() < deadline
            time.sleep(.02)
    finally:
        for client in slow:
            client.close()
        process.terminate()
        process.wait(5)


def test_readonly_handler_constructs_during_writer_without_binding_identity(tmp_path):
    index = tmp_path / 'index'
    with ObservationReader(tmp_path, index_dir=index) as writer:
        (tmp_path / 'runtime-status.json').write_text(json.dumps({'run_id': 'new-run'}))
        writer.db.execute('BEGIN IMMEDIATE')
        writer.db.execute("INSERT INTO meta VALUES ('uncommitted', 'writer')")
        try:
            with ObservationReader(tmp_path, index_dir=index, readonly_index=True) as reader:
                assert reader.status()['run_id'] == 'new-run'
                assert reader.db.total_changes == 0
                assert reader.db.execute("SELECT value FROM meta WHERE key='run_id'").fetchone() is None
                assert reader.db.execute("SELECT value FROM meta WHERE key='uncommitted'").fetchone() is None
        finally:
            writer.db.rollback()


def test_readonly_index_rebuild_cannot_delete_index(tmp_path):
    index = tmp_path / 'index'
    with ObservationReader(tmp_path, index_dir=index):
        pass
    path = index / 'observation.sqlite'
    previous = path.read_bytes()
    with pytest.raises(ValueError, match='readonly'):
        ObservationReader(tmp_path, index_dir=index, readonly_index=True, rebuild=True)
    assert path.read_bytes() == previous
