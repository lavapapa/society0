# 展示数据合同

本文件说明资料文件如何描述一次实验的可见内容。字段只服务呈现，不代表引擎已经具备对应消费者。

## 一、顶层

```json
{
  "schemaVersion": 1,
  "dataKind": "mock",
  "study": { "id": "", "title": "", "question": "", "description": "", "dataRevision": "", "updatedAt": "" },
  "versions": [], "plugins": [], "worlds": [], "runs": [],
  "entities": [], "observations": [], "datasets": [],
  "panels": [], "documents": [], "artifacts": [],
  "workspace": { "pages": ["setup"], "timeModes": ["round"] }
}
```

`dataKind` 取 `mock`、`draft` 或 `recorded`。`dataRevision` 是显式版本号，不计算哈希。

## 一之二、面板与数据集

资料分三层：**数据集 → 面板 → 页面入口**。

- `datasets[]`：`{id, availability, title, source, ...}`，内容按组件类型不同；每个数据集只服务一个面板。
- `panels[]`：`{id, title, type, datasetId, size}`。`type` 取 `catalog.md` 中的组件类型；`size` 取 `s`、`m`、`l`、`full`。
- `workspace.pages`：声明页面顺序，例如 `["setup", "runs", "observe"]`。**只有 `panels` 非空时页面才出现“观察”页**；`atlas: true` 时出现“图鉴”并替代“观察”。

```json
{
  "datasets": [{ "id": "ds-factors", "availability": "ready", "source": "用户选择记录", "title": "因素表",
                 "columns": [{ "key": "factor", "label": "因素" }], "rows": [{ "id": "f1", "factor": "撤离距离" }] }],
  "panels": [{ "id": "p-factors", "title": "设计因素", "type": "table", "datasetId": "ds-factors", "size": "m" }],
  "workspace": { "pages": ["setup", "observe"] }
}
```

设计阶段可以只有 `documents`、`panels` 与 `datasets`，`versions`、`worlds`、`runs`、`entities` 为空。

### 1.3 旧集合的去留

- `documents[]`：早期资料目录，只在需要独立文件清单时使用；专题阅读请直接用 `document` 面板，见 `components/tables-records.md`。
- `artifacts[]`：导入的工件清单，对应 `artifacts` 面板。
- `workspace.panels`：早期把面板放在 `workspace` 内；现已提到顶层 `panels`，旧写法会被读取但新资料统一用顶层字段。

## 二、身份

- `versions[]`：配置版本，`id`、`label`、`configSource`、`config`，可选 `configFields[]`。
- `worlds[]`：`id`、`label`、`versionId`、`parentWorldId`、`plannedSource`、`overrides`。
- `runs[]`：`id`、`label`、`worldId`、`versionId`、`status`、`dataThrough`。
- `observations[]`：`id`、`runId`、`step`、`round`、`businessTime`、`label`。
- `entities[]`：`id`、`label`、`kind`、`group`、`attributes`。

ID 在声明范围内稳定。页面选择与草稿都以这些 ID 恢复。

## 三、状态

资料可用性统一使用 `availability`：`unconfigured`、`awaiting`、`loading`、`empty`、`partial`、`ready`、`error`、`stale`。运行状态使用独立字段 `status`：`queued`、`running`、`paused`、`completed`、`failed`、`cancelled`、`unknown`。

两者不能互相代替：资料部分可用不代表运行失败，运行完成也不代表所有资料都可读。

## 四、配置字段

`configFields[]` 描述可编辑参数：

```json
{ "path": "/environment/tick_days", "label": "每步自然日", "type": "number", "description": "..." }
```

`path` 是 JSON Pointer。`type` 取 `text`、`number`、`boolean`、`select`。`select` 用 `options` 列出允许值，元素可以是字符串或 `{value,label}`。

## 五、时间

同一条记录可以同时带业务时间、完整步骤和执行时间：

- `businessTime`：仿真的自然日或回合；
- `step` / `round`：完整步骤编号；
- `executedAt`：真实执行时间。

选择某个业务日期时只展示该范围已有的记录，不用更晚的快照补历史。

## 六、来源与单位

数据集与指标应带 `scope`、`unit`、`total`、`loadedRange` 和 `sourceRefs`。零值、未提供、未采集分别表达；表格的 `total` 未知时写 `null`，界面显示“总数未知”。
