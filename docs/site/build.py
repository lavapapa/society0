"""从同一源码树生成版本、接口签名、工具schema与可离线阅读的静态站。"""
from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
API = re.compile(r'<!-- api: ([\w.]+) -->')


def signature_text(node):
    """保留Python语法中的类型、参数位置和默认表达式，不导入可选提供方。"""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        prefix = 'async ' if isinstance(node, ast.AsyncFunctionDef) else ''
        result = f'{prefix}def {node.name}({ast.unparse(node.args)})'
        return result + (f' -> {ast.unparse(node.returns)}' if node.returns else '')
    if isinstance(node, ast.ClassDef):
        bases = ', '.join(ast.unparse(base) for base in node.bases)
        lines = ['@' + ast.unparse(decorator) for decorator in node.decorator_list]
        lines.append(f'class {node.name}' + (f'({bases})' if bases else '') + ':')
        fields = [child for child in node.body if isinstance(child, ast.AnnAssign)]
        lines.extend('    ' + ast.unparse(child) for child in fields)
        init = next((child for child in node.body if isinstance(child, ast.FunctionDef) and child.name == '__init__'), None)
        if init:
            lines.append('    ' + signature_text(init))
        if not fields and init is None:
            lines.append('    ...')
        return '\n'.join(lines)
    return ast.unparse(node)


def resolve_symbol(repo, symbol):
    parts = symbol.split('.')
    for split in range(len(parts) - 1, 0, -1):
        base = repo / 'src' / Path(*parts[:split])
        path = base.with_suffix('.py')
        if not path.is_file():
            path = base / '__init__.py'
        if not path.is_file():
            continue
        node = ast.parse(path.read_text())
        for name in parts[split:]:
            found = next((child for child in node.body if getattr(child, 'name', None) == name), None)
            if found is None:
                raise ValueError(f'{symbol}: 在 {path} 中找不到 {name}')
            node = found
        return path, node
    raise ValueError(f'无法定位源码符号：{symbol}')


def api_block(repo, commit, symbol):
    path, node = resolve_symbol(repo, symbol)
    relative = path.relative_to(repo).as_posix()
    source = f'https://github.com/lavapapa/society0/blob/{commit}/{relative}#L{node.lineno}-L{node.end_lineno}'
    return f'\n### `{symbol.rsplit(".", 1)[-1]}`\n\n```python\n{signature_text(node)}\n```\n\n[查看实现]({source}) · 导入路径：`{symbol}`\n'


def export_tools(python, repo):
    env = {**os.environ, 'PYTHONPATH': str(repo / 'src')}
    output = subprocess.check_output([python, str(ROOT / 'export_tools.py')], env=env, text=True)
    return json.loads(output)


def tool_page(schemas, commit):
    lines = ['# Agent 工具参数', '',
        '本页从当前源码的 `LLMDriver._tools()` 直接生成。构建仅创建策略和工具声明，不连接提供方，也不激活主体。实际请求还受到所选模型与提供方协议约束。', '',
        '工具负责发现环境资料和领域行动。具体业务参数通过 `action_describe` 获取，执行时继续检查主体资格。文件路径与返回原文的关系见[文件接口](filesystem.md)，完成条件见[LLMDriver](llm.md)。', '',
        '## 一、模式与工具集合', '',
        '| 模式 | 工具 |', '|---|---|']
    for name, tools in schemas.items():
        lines.append('| ' + name + ' | ' + (', '.join('`' + t['function']['name'] + '`' for t in tools) or '无工具；使用结构化直接输出') + ' |')
    lines.extend(['', '## 二、默认决定模式', '',
        '以下是默认非严格模式的真实JSON Schema。`required`给出该工具的必填字段；分页游标和修订身份来自实际读取结果。`limit`、字节范围及权限范围各自生效。', ''])
    explanations = {
        'action_find': '按目标资源发现当前可用行动。query对名称和描述做字面子串匹配；空字符串用于分页枚举。',
        'action_describe': '取得指定行动在当前目标上的参数schema和说明，执行前据此构造arguments。',
        'action_invoke': '提交领域行动；环境重验参数和资格，返回completed、accepted或rejected及可用反馈。',
        'ls': '列出当前主体有权发现的直接子项，保留总数、版本及续页游标。从根目录逐级发现路径。',
        'read': '按字节范围读取原文。续读使用返回偏移与修订身份，二进制数据可以base64传输。',
        'find': '按glob模式查找逻辑文件路径；保留完整匹配集合的分页入口。',
        'grep': '在选定路径内搜索原文，支持字面匹配及正则；读取成本随被搜索的原文字节量增长。',
    }
    for tool in schemas['decision']:
        function = tool['function']
        lines.extend(['### `' + function['name'] + '`', '', explanations[function['name']], '',
                      '```json', json.dumps(function['parameters'], ensure_ascii=False, indent=2), '```', ''])
    lines.extend(['## 三、严格模式与测量', '',
        '严格工具模式将动态行动对象与指定游标编码为JSON字符串，并规范必填及空值字段。调用者应使用下方生成声明，避免把普通模式的对象直接发送给严格模式。', '',
        '访谈模式保留结构化提交工具；`direct_json=True`使用直接结构化输出。示例测量schema在构建脚本中明示，实际研究应提供自己的 `result_schema`。', ''])
    for name in ['strict', 'decision-shell', 'interview', 'direct-json']:
        lines.extend(['??? example "' + name + ' 的完整声明"', '', '    ```json'])
        lines.extend('    ' + line for line in json.dumps(schemas[name], ensure_ascii=False, indent=2).splitlines())
        lines.extend(['    ```', ''])
    lines.extend([f'[工具实现](https://github.com/lavapapa/society0/blob/{commit}/src/society0/kernel/llm.py) · [完整schema下载](../assets/tool-schemas.json)', '',
                  '模型的工具调用原文与执行结果由Thread保存。运行预算耗尽、输出截断及业务拒绝的含义见[决定策略](llm.md)。'])
    return '\n'.join(lines)


def build(args):
    import yaml
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip()
    project = tomllib.loads((REPO / 'pyproject.toml').read_text())['project']
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    site_dir = (output / 'site').resolve()
    assert site_dir == output / 'site', '构建清理目标必须是输出目录中的site，不能经由符号链接跳转'
    print(f'确认MkDocs重建目标：{site_dir}')
    config = yaml.safe_load((ROOT / 'mkdocs.yml').read_text())
    schemas = export_tools(args.api_python, REPO)
    with tempfile.TemporaryDirectory(prefix='society0-docs-') as temporary:
        temp = Path(temporary)
        stage = temp / 'content'
        shutil.copytree(ROOT / 'content', stage)
        shutil.copy2(ROOT / 'vendor/iframe-worker-LICENSE', stage / 'assets/iframe-worker-LICENSE')
        for page in stage.rglob('*.md'):
            text = API.sub(lambda match: api_block(REPO, commit, match[1]), page.read_text())
            page.write_text(text.replace('@COMMIT@', commit))
        (stage / 'start/version.md').write_text(f'''# 版本与来源

本站面向 Society0 Next（猎龙），覆盖通用内核、plain、cognition、round_robin 与全部 Social 插件。

## 一、构建身份

| 项目 | 值 |
|---|---|
| Python包版本 | `{project['version']}` |
| 源码提交 | `{commit}` |
| Python要求 | `{project['requires-python']}` |
| 开发路线 | `next` |

[查看同一源码提交](https://github.com/lavapapa/society0/tree/{commit})。本站的接口签名、工具schema及版本信息来自该源码树。

## 二、选择来源

安装与复现研究时显式固定Next源码及依赖。既有稳定架构在 `stable/4.1` 维护，具有独立的接口与运行格式。每次研究保存源码身份、输入配置、模型服务设置和完整运行工件。

页面中的源码链接固定在本站构建提交。运行状态与完整步骤从实际运行工件核实。

## 三、使用范围

基础规则运行支持macOS或Linux的本地持久存储；可选提供方、记忆、社交、Shell和HTTP能力分别安装。具体要求见[安装](installation.md)与[可选依赖参考](../reference/installation.md)。

本站提供可静态部署的HTML和完整离线包，离线包保留搜索与相对链接。外部源码及第三方文档链接在联网后访问。
''')
        (stage / 'reference/tools.md').write_text(tool_page(schemas, commit))
        (stage / 'assets/tool-schemas.json').write_text(json.dumps(schemas, ensure_ascii=False, indent=2))
        config.update(docs_dir=str(stage), site_dir=str(output / 'site'), copyright='Society0 Next · 中文技术文档')
        config_file = temp / 'mkdocs.yml'
        config_file.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
        subprocess.run([sys.executable, '-m', 'mkdocs', 'build', '--strict', '--config-file', str(config_file)], check=True)
        # MkDocs的404模板使用站点根路径，无法随离线包迁移。
        not_found = (output / 'site' / '404.html').resolve()
        assert not_found.parent == (output / 'site').resolve()
        print(f'移除离线包不使用的根路径404页面：{not_found}')
        not_found.unlink()
        with ZipFile(output / 'society0-docs-html.zip', 'w', ZIP_DEFLATED) as archive:
            for path in sorted((output / 'site').rglob('*')):
                if path.is_file():
                    archive.write(path, path.relative_to(output / 'site'))
        manifest = {'commit': commit, 'version': project['version'], 'pages': len(list(stage.rglob('*.md'))),
                    'source': str(ROOT), 'site': str(output / 'site')}
        (output / 'build-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        print(json.dumps(manifest, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='站点及ZIP的输出目录')
    parser.add_argument('--api-python', default=sys.executable, help='安装Next llm依赖的Python，用于离线导出工具schema')
    build(parser.parse_args())
