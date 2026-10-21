# 自定义面板示例

`issue-matrix.jsx` 是一个最小自定义面板：接收 `dataset` 与共享 `select`，点击行可定位到主体。

接入步骤：

1. 在 `data.json` 的 `datasets` 里提供数据；
2. 在 `panels` 里声明：

```json
{ "id": "p-issues", "title": "议题与证据", "type": "custom", "renderer": "issueMatrix", "datasetId": "ds-issues", "size": "l" }
```

3. 在构建入口把组件挂到 `window.SOCIETY0_PANELS.issueMatrix`；
4. 重新构建并刷新。

面板错误只影响本格，其余面板继续可用。
