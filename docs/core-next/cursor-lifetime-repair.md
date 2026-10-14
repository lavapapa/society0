# 产业增长验收触发的游标释放修复

本记录保存独立 Next 修复分支的风险、逐文件改动与验收入口。产业规范写入约五十二次 SQL 调用形成一笔真实关闭货流；十万笔试验发现峰值 RSS 超出一 GiB 配置。独立 APSW 最小复现中，十万次显式 `Cursor.close()` 留下约二十 MB Python 对象；原生析构与单游标复用没有这一保留量，强制关闭仍可复现。

## 变更

- [存储服务](<../../src/society0/kernel/storage.py>)：`ReadView.query`、`iter_query`、`Writer.execute`、`executemany` 在完成后解除游标最后引用，交给 APSW 析构；迭代器从活动集合移除。`ReadView._finish` 重置尚在迭代的语句，随后保留作用域失效检查，释放 SQL 锁与绑定参数。
- [游标生命周期用例](<../../tests/primary/test_kernel_cursor_lifetime.py>)：五条路径各重复二千次，保留内存低于六十四 KB；逃逸迭代器在失效后仍抛出 `StorageError`。

## 验证

`artifacts/cursor-lifetime/red.txt` 保存五项原实现失败；`green.txt` 保存五项通过。`deterministic-clean-env.txt` 保存完整 primary suite 全绿退出零。前两次全量执行分别遭遇外部 SOCKS 代理与 NO_PROXY IPv6 解析污染，保留失败日志；最终确定性进程剥离代理环境后验证所有原消费者。

运行命令：

```bash
env -u ALL_PROXY -u HTTP_PROXY -u HTTPS_PROXY -u NO_PROXY \
  -u all_proxy -u http_proxy -u https_proxy -u no_proxy \
  PYTHONPATH="$PWD/src" /path/to/core-python -m pytest tests/primary -q
```

[独立端到端](<../../artifacts/cursor-lifetime/independent-review.md>)通过真实 RunPlan 前缀、恢复、只读、嵌套/早停/逃逸读取及回滚后继续写入。两轮四千 SQL 闭后各保留约一 KB，显式关闭对照仍复现泄漏。另发现 callback 提前停止 `UPDATE RETURNING` 时提交先于 scope 收束而触发真实 BusyError；[补充红灯](<../../artifacts/cursor-lifetime/red-pending-write.txt>)复现后，在 callback 返回时结束作用域并重置未完成语句，再提交事务；异常路径仍由 finally 收束。对应测试核对两行权威更新、逃逸生成器失效和下一 Writer。

产业固定提交后的真实增长复测由相应产物记录。基础库修复保持业务模型、SQL 表与公开读写接口；原稳定版本线继续独立维护。
