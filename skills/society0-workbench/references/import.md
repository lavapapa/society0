# 按需接入资料

## 一、选择资料形状

先判断用户要看什么，再决定数据结构：

| 阅读问题 | 形状 |
|---|---|
| 谁与谁有关系 | `nodes` / `edges` |
| 有哪些内容与回复 | `posts` |
| 指标随时间变化 | `series` / `forecast` |
| 世界之间差异 | `worlds.series` |
| 日程与资源 | `events` / `timeline` / `gantt` |
| 空间与运输 | `nodes(x,y)` / `routes` / `tracks` |
| 主体经历 | `messages` / `records` |

## 二、只取需要的

- 每个 `datasetId` 对应一份独立数据，面板只引用自己需要的那份；
- 大表保留 `total` 与 `loadedRange`，正文按需展开；
- 不把整个运行的数据库直接塞进 `data.json`，先转换成展示切片；
- 切片范围写进 `source`，用户能看到数据来自哪里。

## 三、缺资料的写法

```json
{ "id": "ds-x", "availability": "awaiting", "note": "等待主体配额资料" }
```

`availability` 取 `unconfigured`、`awaiting`、`loading`、`empty`、`partial`、`ready`、`error`、`stale`。数值零仍是零。

## 三之二、放置层级

- 数据集放顶层 `datasets`，面板放顶层 `panels`，页面顺序写 `workspace.pages`；
- 面板用 `datasetId` 连接数据集，一个面板一份数据；
- `panels` 非空才会出现“观察”页；设计阶段可以只有 `document` 与 `table`；
- 不要把面板嵌进 `datasets` 或 `workspace.panels`。

## 四、面板声明

```json
{ "id": "p-stock", "title": "企业库存", "type": "table", "datasetId": "ds-stock", "size": "l" }
```

`size` 取 `s`、`m`、`l`、`full`。面板顺序即渲染顺序，用户可在页面调整尺寸与顺序，结果保存在浏览器。

## 五、检查

重新生成后打开页面，确认：新面板出现、缺资料面板显示等待、没有伪数据、来源可见。
