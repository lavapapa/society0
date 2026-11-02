# SQLite native

本包为Society0的数据库worker提供同步SQLite原生驱动。普通SQL、statement批执行、Session、BLOB和备份共用同一个SQLite库实例；类型与公开入口见[src/index.d.ts](src/index.d.ts)和[src/index.mjs](src/index.mjs)。支持范围及失败语义见[SPEC](SPEC.md)，上层接入事项见[INTEGRATION](INTEGRATION.md)。

## 一、构建

已验证Linux x86_64/glibc。源码构建需要gcc、g++、Python3及包含headers的Node24发行版。在源码仓库的包目录执行：

```bash
python3 -B build.py --out-dir ../../.build/native-work/sqlite-native
```

[构建器](build.py)使用随包提供的[官方SQLite源码](vendor/source/sqlite3.c)和[编译配方](vendor/BUILD.json)，离线重建SQLite共享库及N-API模块；原始来源为[SQLite 3.49.1公开发布](https://www.sqlite.org/2025/sqlite-amalgamation-3490100.zip)。Node headers默认按当前node安装位置查找，可显式设置NODE_INCLUDE_DIR。输出目录位于源包之外，包含 `build/sqlite_native.node`、`vendor/libsqlite3.so` 和 `NATIVE-BUILD.json`；两个二进制在该目录内原子替换。原始[来源声明](vendor/SOURCE.json)保持不变，本次实际编译选项、编译器、Node版本与输出清单写入输出目录的构建记录。可消费包由仓库根构建器装配；复用校验使用留存参考输出的实际字节。官方amalgamation的可选UPDATE/DELETE LIMIT语法未纳入本构建，其余SQL功能以本次构建记录的实际编译选项为准。macOS、Windows和其他ABI尚未验收。

## 二、使用

连接由一个worker拥有。下面示例直接调用同步API；宿主通过自己的RPC队列调度worker。

```js
import {openConnection, createInterruptControl} from '@society0/sqlite-native'
const control = createInterruptControl()
const db = openConnection({path: 'world.sqlite', control, maxInlineBytes: 65536})
db.execSchema('CREATE TABLE note(id INTEGER PRIMARY KEY, body TEXT)')
db.startSession(['note'])
db.begin({maxDurationMs: 1000, maxCapturedBytes: 1048576})
try {
  db.execute('INSERT INTO note VALUES(?, ?)', [1n, 'hello'])
  db.commit()
} catch (error) {
  db.rollback()
  throw error
}
const segment = db.exportSegment('segment-1.pending', {chunkBytes: 65536})
const rows = db.query('SELECT * FROM note', [], {maxRows: 10, maxBytes: 4096})
db.close()
```

exportSegment输出新建的暂存文件并释放成功导出的Session。文件同步、完整点描述符与原子发布由历史服务执行。下一事务需重新startSession，表清单在同一schema生命周期内复用验证结果。

SQLINTEGER返回bigint，REAL返回number，BLOB返回独立Uint8Array副本。参数为位置数组。query使用sqlite3_stmt_readonly；execute/executeMany需要显式事务。普通SQL经原生authorizer限制事务、savepoint、PRAGMA和DDL，bootstrap使用独立execSchema入口。单statement后可附空白与注释。外键验收使用checkForeignKeys()：先通过SQLite原生prepare检查空表的父表/父键，再检查实际行违例并定位表、父表和rowid。

## 三、取消

把control共享给宿主。宿主通过Atomics.store(control, 0, 1)取消正在执行的SQL或流；原生progress handler和流回调直接读取控制字。取消不依赖worker事件循环处理消息。等待真实调用返回或worker退出后，再由宿主清零并启动下一操作。

控制字表示连接当前操作的取消域；其所有权和派发序号由RPC层管理。SQL计算在VM progress检查点响应，流在IO块之间响应；底层操作系统IO等待仍需等系统调用返回。超时使用同一原生检查路径。close释放连接资源，资源管理器应先等待已派发调用的物理结算。

## 四、预算

构造参数、批输入、查询行数/字节和BLOB范围都有明确预算；原生在创建JS行对象前检查结果字节。maxInlineBytes传给sqlite3_limit，maxCapturedBytes作为捕获中断阈值。领域正文采用对象引用；原生输出块可能携带同一行的旧值与新值，实际文件IO再按chunkBytes分块。

addon通过进程静态once机制设置默认64MiB SQLite hard heap cap，后续worker加载保留已有设置。configureHeapLimit作用于进程内同一SQLite库实例的全部连接，由宿主统一配置者或独立实验显式调整。V8、Buffer、压缩器及独立加载的SQLite引擎另外计量。state报告事务、原生autocommit、Session退役和dirty状态；OOM原Session退役，必须先rollback结算。导出失败后的已提交状态保持dirty并拒绝新写，交由上层恢复。close不发布完整点。

受支持的捕获对象是显式非空主键的普通表；INTEGER PRIMARY KEY rowid别名自动满足要求。可空主键、虚拟表和生成列会被明确拒绝。restore临时暂停目标业务触发器并使用FKNOACTION，冲突中止、外键验证与savepoint回滚完成后恢复触发器设置。
