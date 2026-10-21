# 空壳示例

- 打开：`index.html`
- 资料：`data.json`（mock，revision 0）
- 模板：随包 `assets/shell.html`

用途：检查第一次对话时的空白工作台。实验设置为空，插件与平行世界入口固定位于前两项；主体、运行与结果都还没有资料。

修改 `data.json` 后执行：

```bash
python3 ../../../scripts/render_workbench.py --project .
```

刷新 `index.html` 即可看到新资料。
