"""公共工厂的惰性导入与类型声明保持一致。"""
import subprocess
import sys


def test_composable_public_api():
    code = '''
import ast
import sys
from pathlib import Path
import society0
import society0.plugins as plugins
assert not any(name.startswith('society0.kernel.') for name in sys.modules)
assert not any(name.startswith('society0.plugins.') for name in sys.modules)
from society0.plugins import plain_plugin
assert callable(plain_plugin)
assert 'society0.plugins.social' not in sys.modules
assert 'society0.kernel.llm' not in sys.modules
assert 'chromadb' not in sys.modules
module = ast.parse(Path(plugins.__file__).read_text())
typed = {alias.asname or alias.name for node in module.body
         if isinstance(node, ast.If) and isinstance(node.test, ast.Name)
         and node.test.id == 'TYPE_CHECKING'
         for statement in node.body if isinstance(statement, ast.ImportFrom)
         for alias in statement.names}
assert set(plugins.__all__) == typed
for name in plugins.__all__:
    assert name in dir(plugins)
    assert callable(getattr(plugins, name)), name
'''
    subprocess.run([sys.executable, '-c', code], check=True)
