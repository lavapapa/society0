"""构建辅助函数的最小确定性检查；不调用模型或构建站点。"""
import ast
from pathlib import Path
from tempfile import TemporaryDirectory
from build import api_block, signature_text, resolve_symbol

node = ast.parse('async def choose(x: str, /, *, limit: int = 10) -> list[str]: pass').body[0]
assert signature_text(node) == 'async def choose(x: str, /, *, limit: int=10) -> list[str]'
node = ast.parse('class Item:\n    name: str\n    count: int = 0').body[0]
assert 'name: str' in signature_text(node)
with TemporaryDirectory() as temporary:
    repo = Path(temporary)
    (repo / 'src/demo').mkdir(parents=True)
    (repo / 'src/demo/core.py').write_text('class Store:\n    def read(self, *, size=20): pass\n')
    path, method = resolve_symbol(repo, 'demo.core.Store.read')
    assert method.name == 'read' and method.lineno == 2
    rendered = api_block(repo, 'test-revision', 'demo.core.Store.read')
    assert 'size=20' in rendered and '#L2-L2' in rendered
    try:
        resolve_symbol(repo, 'demo.core.Store.missing')
    except ValueError:
        pass
    else:
        raise AssertionError('不存在的公开符号必须使构建失败')
print('文档构建检查通过：签名、字段、方法定位与缺失符号失败')
