# 插件组合构建

研究者通过同一组 Plugin 声明选择共享环境中的机制。每个插件携带自己的静态表结构、初始数据与运行服务；组合构建器建立共同运行目录，然后按照已声明依赖安装服务。插件主机继续负责依赖和生命周期，存储类型由这一具体构建入口确定。

## 一、声明

`Plugin(name, requires=(), install=..., schema=(), initialize=None)` 的 schema 是 SQL DDL 字符串序列，initialize 是同步 Writer 回调。表名与索引名由插件工厂按实例明确命名，同类机制多实例可以声明不同前缀。重复表结构由 SQLite 报错；构建器没有改写插件 SQL。requires 同时确定初始化与服务安装顺序。

```python
plugin = Plugin(
    'market', requires=('storage',),
    schema=('CREATE TABLE market_orders(id INTEGER PRIMARY KEY, amount INTEGER)',),
    initialize=lambda writer: writer.execute('INSERT INTO market_orders VALUES(1, 7)'),
    install=lambda ctx: ctx.provide('store', ctx.require('storage', 'store')),
)
async with compose(run_dir, [plugin]) as host:
    market_store = host.service('market', 'store')
```

storage 是构建器提供的保留插件实例名，其 store 服务是同一个 StageStore。PluginHost 单独使用时携带这些声明，执行 install；它没有数据库依赖，也不会自行执行 schema 或 initialize。

## 二、构建

`compose(path, plugins, source=None, step=None)` 是异步上下文管理器，返回已安装的 PluginHost。先检查重复实例、缺失依赖和依赖环，再汇集 DDL。所有 initialize 在同一个初始事务中按依赖顺序运行；异常或异步初始化使构建失败，目标目录尚未发布。初始状态完成后才安装运行服务。

恢复使用 `compose(new_path, plugins, source=old_path, step=1)`。声明 schema 通过内存 SQLite 编译后与源运行的规范定义比较；不匹配时尚未复制源数据。恢复保留完整检查点的状态，并跳过 initialize。安装服务时可以重建缓存和连接，业务初始数据继续来自恢复状态。

服务退出采用主机的反向依赖清理，随后关闭 StageStore。安装失败仍会清理已安装资源；此时已经建立的完整初始检查点保留在目标目录，供诊断或明确恢复。退出组合上下文不会自动发布运行中的步骤，完整步骤由 Runtime 完成。

## 三、验收

`tests/primary/test_kernel_composition.py` 覆盖两个插件的各自 DDL、依赖初始化、同一 writer、恢复跳过初始化、schema 不匹配提前失败、安装失败清理和异步初始化拒绝。`research/core-next/composition-red.txt` 保留缺实现的首轮失败，随后通过记录在 composition-green.txt 与 composition-models-review-green.txt。后续 Actor、内置环境和完整运行入口使用同一组合流程，其各自语义继续由对应测试验收。
