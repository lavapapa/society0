# Society0 Next 技术文档站

本站使用 MkDocs Material，覆盖Next通用内核与非产业内置能力。正文、配置、构建和检查脚本随源码版本维护；生成HTML和离线包放在显式输出目录。

## 一、源文件

- [站点配置](mkdocs.yml)维护唯一导航，页面按入门、教程、概念、指南、参考与研究资源分组。
- [正文首页](content/index.md)及相邻Markdown维护面向用户的说明。接口页使用 `<!-- api: society0.kernel.plugins.Plugin -->` 标记，由[构建脚本](build.py)从当前源码AST提取签名、字段和准确定位。
- [工具导出](export_tools.py)通过真实LLMDriver生成策略变体的JSON Schema，创建声明时不调用模型。生成的版本页与schema均读取本站所在仓库。
- [样式](content/assets/site.css)、离线Worker shim及许可证保持随包提供。字体采用系统字体，离线浏览与搜索可用。
- [重构计划](PLAN.md)记录目录责任、参考项目与本次验收范围。长期API语义正文与历史审查记录分别维护。

## 二、构建

先按仓库安装说明准备已包含 `llm` 依赖的Core Python环境，供工具声明导出使用。文档构建依赖在独立环境安装，从仓库根目录执行：

```sh
uv venv --python 3.12 docs/site/.venv
uv pip install --python docs/site/.venv/bin/python -r docs/site/requirements-docs.txt

docs/site/.venv/bin/python docs/site/check_build.py
docs/site/.venv/bin/python docs/site/build.py \
  --api-python "$PWD/.venv/bin/python" \
  --output /absolute/path/to/society0-docs

docs/site/.venv/bin/python docs/site/check_site.py /absolute/path/to/society0-docs/site
```

把输出路径改为自己的文档构建目录。输出包含 `site/`、`society0-docs-html.zip` 与 `build-manifest.json`。每次严格构建更新该目录内的站点和压缩包；模板展开在临时目录完成，手写正文保持原样。

`--api-python`可以指向另一个安装了相同Next依赖的Python环境；导出子进程会通过PYTHONPATH读取本站所在仓库的实际代码。构建环境只用于Markdown渲染，模型与Shell可选能力不会在构建时启动。

## 三、验证与发布

修改页面后核对实际源码、默认值及示例，先运行最小构建函数检查，再严格构建并检查内部链接、资源和锚点。教程的完整无模型程序应实际执行；模型配置示例需说明端点与依赖前提。

交付前通过浏览器检查首页路径、搜索、复制、明暗模式、移动目录及至少一条完整教程。直接文件打开与HTTP访问均需检查中文搜索；将ZIP解压到新目录后重新验证，确保资源与相对路径完整。

发布时把 `site/` 目录部署到静态HTTP服务器。本站使用 `.html` 链接，离线包与HTTP站点采用同一套路径。公网URL、服务器状态和实测结果写在相应发布记录中。站内源码链接固定到构建提交，正式交付前将对应源码提交推送到远端Next。

## 四、Read the Docs

仓库根目录的 [Read the Docs 配置](../../.readthedocs.yaml) 使用平台的[自定义构建步骤](https://docs.readthedocs.com/platform/stable/build-customization.html)。导入 `lavapapa/society0` 时将默认源码分支设为 `next`，配置路径为 `.readthedocs.yaml`，文档语言设为简体中文。GitHub App 的仓库访问范围选择 Society0；每次向启用版本对应的分支推送后，由集成触发重新构建。

云端沿用同一个构建器，先生成真实接口与工具声明，再严格渲染和检查全站引用。声明导出环境从现有锁文件提取Python依赖，省去没有执行消费者的原生文件工具；Markdown渲染环境安装文档的独立固定依赖。这两个环境分别保留自己的版本约束，无需修改产品锁文件。

构建将HTML放入平台的 `html` 输出，将完整离线包放入 `htmlzip` 输出，并在站点根目录提供 `society0-docs-html.zip` 下载。平台的默认版本、GitHub连接及公开站点URL在项目后台管理；构建成功后检查首页、版本页与中文搜索，再确认发布完成。
