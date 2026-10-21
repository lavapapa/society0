# 新增面板

## 一、先找最接近的组件

读 `catalog.md`，选一个数据形状最接近的组件，复制它的 mock 与声明，再决定是否需要新组件。

## 二、最小自定义面板

```jsx
export function MyPanel({ dataset, selection, select }) {
  if (!dataset) return <p>没有绑定资料。</p>;
  return <ul>{dataset.rows.map((row) => (
    <li key={row.id} onClick={() => select({ entityId: row.entityId })}>{row.label}</li>
  ))}</ul>;
}
```

注册：

```js
window.SOCIETY0_PANELS = { myPanel: MyPanel };
```

面板声明：

```json
{ "id": "p-my", "title": "我的视图", "type": "custom", "renderer": "myPanel", "datasetId": "ds-my" }
```

## 三、必须遵守

- 使用 `dataset` 与共享 `select`，不直接读全局运行目录；
- 缺资料显示等待，不用假数据补位；
- 数值带单位与来源；
- 长内容可完整读取；
- 关闭或切换时清理定时器、监听与图实例；
- 错误只影响本面板。

## 四、何时进入公共组件库

同一形状被第二个真实场景需要时，把组件移入 `src/` 并登记到 `catalog.js`，同时补示例与文档。一次性视图留在实验目录。
