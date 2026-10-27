# Society0 Next 技术文档站

本站使用 MkDocs Material，覆盖通用 Core、plain、cognition、round_robin 与全部 Social 插件。正文随源码维护；HTML、工具 schema 和离线包由构建脚本生成。

## 一、内容

[站点配置](mkdocs.yml)维护导航；[正文首页](content/index.md)通向安装、教程、概念、指南和 Python API。接口页中的 `<!-- api: society0.kernel.plugins.Plugin -->` 标记由[构建脚本](build.py)从源码 AST 提取签名、字段和定位。

[工具导出](export_tools.py)从 LLMDriver 生成工具 JSON Schema，创建声明时无需调用模型。版本页读取项目元数据，避免手工复制版本信息。[写作规范](WRITING.md)定义正文责任与用词；[样式](content/assets/site.css)、离线 Worker 和许可证随站点提供。

## 二、构建

先按[安装指南](content/start/installation.md)准备项目环境，再在仓库根目录安装独立的文档依赖：

```sh
uv venv --python 3.12 docs/site/.venv
uv pip install --python docs/site/.venv/bin/python -r docs/site/requirements-docs.txt

docs/site/.venv/bin/python docs/site/check_build.py
docs/site/.venv/bin/python docs/site/build.py \
  --api-python "$PWD/.venv/bin/python" \
  --output "$PWD/docs/site/.build"
docs/site/.venv/bin/python docs/site/check_site.py "$PWD/docs/site/.build/site"
```

输出包含 `site/`、`society0-docs-html.zip` 和 `build-manifest.json`。每次构建重建指定输出目录下的站点；Markdown 展开在临时目录中完成。`--api-python` 指向安装相同 Next 依赖的 Python，导出进程通过 PYTHONPATH 读取当前仓库源码。

## 三、检查

修改页面后，核对实际源码、默认值、示例与链接，运行构建函数检查、严格构建和站点引用检查。教程里的规则程序按实际命令执行；远端模型示例需要独立的账号授权。

浏览生成的首页、教程和参考页，检查搜索、代码复制、明暗模式与移动目录。离线包解压后使用相对链接浏览；外部源码链接需要网络。仓库的 [Read the Docs 配置](../../.readthedocs.yaml)复用同一构建器，实际构建身份以生成的版本页为准。
