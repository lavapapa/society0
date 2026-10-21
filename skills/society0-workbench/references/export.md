# 离线导出

默认交付是单个 HTML，双击即可打开，不依赖 CDN、本地服务或模型端点。

## 一、页面从哪来

安装副本已经带好构建产物 `assets/shell.html`，不需要 Node，也不需要源码工程：

- 直接打开 `assets/examples/atlas/index.html` 查看组件；
- 需要自己的页面时用 `scripts/create_workbench.py` 生成，再用 `scripts/render_workbench.py` 刷新。

只有修改组件源码时才需要 Core 仓的 `tools/workbench-universal`：`npm run build` 产出 `dist/index.html`，再复制为 `assets/shell.html`。安装副本不含该工程。

## 二、项目级导出

```bash
python3 <skill>/scripts/render_workbench.py --project <project>/workbench
```

该命令把项目 `data.json` 写入模板的 `#wb-data` 节点后输出 `index.html`。

## 三、导出范围

页面与资料中记录：

- 所选运行、世界、时点、主体与过滤条件；
- `dataKind`（mock / recorded / draft）；
- `study.dataRevision`；
- 未包含的对象或文件。

资料很大时，可以选择只导出当前观察范围；被排除的内容要在页面中说明，而不是静默丢弃。

## 四、已知边界

- 默认单文件内联全部资料，体积随资料增长；
- 需要完整多文件观察包时，输出静态目录并说明打开方式；
- 导出文件不包含真实实验控制能力，也不包含 AI 批注。
