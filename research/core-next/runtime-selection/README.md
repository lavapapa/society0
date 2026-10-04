# 有限实验复现

从 Society0 仓库根执行。既有 `.venv` 需具 Bashkit 0.18.2 与本仓已安装的 society0_filesystem 原生桥；实验不改产品依赖、不调用提供方。

```sh
.venv/bin/python research/core-next/runtime-selection/probe.py
.venv/bin/python research/core-next/runtime-selection/taskgroup_boundary_probe.py
.venv/bin/python research/core-next/runtime-selection/filesystem_probe.py
.venv/bin/python research/core-next/runtime-selection/count_sources.py
npm install --prefix /tmp/society0-runtime-selection-20261004 --ignore-scripts --no-audit --no-fund just-bash@3.6.0
node research/core-next/runtime-selection/just_bash_probe.mjs /tmp/society0-runtime-selection-20261004/node_modules/just-bash/dist/bundle/index.js
uv venv --system-site-packages --python .venv/bin/python /tmp/society0-runtime-selection-20261004/asgi-env
uv pip install --python /tmp/society0-runtime-selection-20261004/asgi-env/bin/python starlette==1.7.0 uvicorn==0.52.1 httpx2==2.13.1
/tmp/society0-runtime-selection-20261004/asgi-env/bin/python research/core-next/runtime-selection/asgi_fts_probe.py
```

同目录 JSON 保存本次 stdout。ASGI 探针初次缺 httpx2、第二次缺用于报告版本的 uvicorn，补齐隔离环境后成功；两次均未触及产品。Bashkit 首次探针误假定 tail 支持 -c，改为明确记录不支持，再以实际支持的 -n 验证分片。本文无产品测试通过数量声明。AnyIO 原始 Task.cancel 穿透屏蔽是有意保留的反例。内存中的完整 ASCII fixture 不代表大数据内存性能测试。
