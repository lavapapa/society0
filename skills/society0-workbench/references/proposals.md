# 参数提议

页面里的参数修改只形成草稿，不写实验文件。用户复制请求，交给 Agent 核对后创建新配置版本。

## 一、提议格式

```json
{
  "type": "society0_config_change_request",
  "baseVersionId": "v003",
  "baseVersionName": "v003 · 含政策公告",
  "configSource": "scenario/config/v003.yaml",
  "changes": [
    { "op": "replace", "path": "/environment/tick_days", "before": 7, "after": 14 }
  ],
  "instruction": "请核对原值后创建新配置版本并更新工作台，暂时不要运行实验。"
}
```

`op` 取 `add`、`remove`、`replace`；`path` 是 JSON Pointer。

## 二、处理步骤

1. 用 `baseVersionId` 和 `configSource` 定位当前实验文件。
2. 逐项核对 `before` 是否与源文件一致；不一致时先说明冲突，不猜测。
3. 确认修改符合实验约束后，创建新的配置版本目录，保留旧版本及其运行。
4. 更新展示资料并重新生成工作台；除非用户明确要求，不启动运行。

## 三、界面要求

- 草稿与原始值同时可见；
- 未发送修改有明确计数；
- 复制成功给出反馈；剪贴板不可用时展示同一份完整文本供手动复制；
- 放弃草稿需要确认，且不影响已保存的资料；
- 草稿按配置来源隔离，切换版本不会串用。
