# 冷批次对照

本组从已转换真实根取有限子集，保留原始声明记录，不重新构造旧 World。基础版本为 6a55d56；其后分组和原生 JSON 编码各自测量，完整目录占盘与输入驻留分别报告。

## 一、基础

原转换库前 10000 条包含最大约 29.95 MB 的记录，原 JSON 字节总计 276257519 B。基础 Dataset 单批生成一个不可变 SQLite 工件，源目录 61.625 MB；可写恢复和只读准备目录逻辑大小分别约 61.625 MB 与 61.588 MB。三目录按路径累计分配 184.885 MB，按设备/inode 去重后为 61.776 MB。源正文由硬链接共享，只读目录没有 root.sqlite。所有 10000 条逐值比较相等。

导入 69.22 s，原 Python JSON 遍历与 zlib 压缩合计 66.10 s，源块解压及 JSON 构建 2.73 s，SQL 和控制约 0.377 s，显式文件同步约 4.84 ms。完整发布 1.11 ms、恢复 4.75 ms、只读准备 3.90 ms，100 页读取 21.82 ms，Session 捕获 2352 B。峰值 356 MB 包含输入单条巨记录的构建及验证，不作为冷观察者内存。同步计数仅覆盖 Python 显式 fsync，未统计原生 SQLite 自行调用的同步系统调用。记录见 cold-datasets-real-subset-20261004.json。

## 二、布局

八个分散连续窗口的原始 JSON 字节直接输入独立 SQLite 文件，逐记录基线使用产品相同的 records/chunks 主键布局；分组实验使用原生 records、spans、blocks 表，索引和页成本均计入文件。窗口原文合计约 4.345 MB。逐条 zlib 库共 3.248 MB，64 KiB zlib 分组共 0.934 MB，64 KiB zstd 分组共 0.901 MB。单纯换逐条 zstd 为 3.396 MB，没有空间收益。

800 次随机全文读取，复用连接时逐条 zlib、分组 zlib、分组 zstd 分别为 17.10、90.58、34.61 ms；每次打开新连接分别为 66.87、177.62、93.95 ms。分组实际解压约 50.99 MB，独立记录为 0.869 MB，随机读取放大真实存在。zstd 组在本小样本中的绝对增量约每次新连接读取 34 微秒，换取约 72.3% 的 SQLite 文件缩减；文件页缓存未清除，尚未代表物理冷盘。写入包含文件 fsync 分别为 127.01、44.12、28.98 ms，进程峰值约 25 MB。结果见 cold-sqlite-codec-real-20261004.json。

正式候选采用更简单的全局固定块边界：每记录保存原文起点和长度，原生块表按整数主键寻址，省去实验 spans 表。每个数据集批次一个文件，解压器按请求创建和释放，没有跨片段永久缓存。其完整真实数据结果应独立记录，不能用窗口比例替代全根最终占盘。

## 三、编码

python-rapidjson 1.25 以独立临时依赖测试，未在初次实验时修改产品依赖。官方 [dump 接口](https://python-rapidjson.readthedocs.io/en/latest/dump.html)支持有界写入回调，[NM_NONE](https://python-rapidjson.readthedocs.io/en/latest/api.html)保留任意精度整数。实验关闭非有限浮点、字节自动解码与通用迭代器扩展，验证 Unicode、大整数、浮点、负零、列表顺序及非法值。各路线均在计时外完整读取并比较。

真实最大 29.948 MB 记录，Python 流编码 7.137 s，标准库整条 dumps 0.296 s，RapidJSON 流式 dump 0.108 s，RapidJSON 整条 dumps 0.210 s。原生流每次写入最多 64 KiB，编码后 RSS 从约 116.8 MB 到 119.8 MB，未超过输入解析形成的 206.1 MB 历史峰值。这里不能从历史高水位推断实际临时分配为零，峰值和驻留点分别记录于 native-json-real-record-20261004.json。

原生流仍会产生输入 Unicode 缓存：[RapidJSON 字符串分支](https://github.com/python-rapidjson/python-rapidjson/blob/v1.25/rapidjson.cpp#L2326)调用 PyUnicode_AsUTF8AndSize，[Python 文档](https://docs.python.org/3.12/c-api/unicode.html#c.PyUnicode_AsUTF8AndSize)规定缓存与原字符串共同存活。实际“汉🙂”重复一百万次，输入对象 sizeof 从 8000060 B 增至 15000061 B，增加量恰为完整 UTF8 加 NUL 的 7000001 B；第二次编码不再增长，释放输入后追踪分配仅余 916 B。因此 64 KiB 写入块约束输出缓冲，整体驻留仍包含输入字符串的 UTF8 缓存；证据与复跑模式为 native-json-utf8-cache-20261004.json、core_next_native_json_probe.py --mode utf8_cache。

本组支持用成熟原生 push 流替换逐原子 Python 遍历，并公开单值与输入 UTF8 缓存边界。无需为了维持旧 generator 外形添加线程队列或临时全量文件；实际 SQL 正文消费者可以在同步回调中接收块。普通对象导入性能与直接复用已封存 JSON 字节的转存性能继续分列，后者不替代前者。

## 四、接入

正式 zstd 分组基线为 f4d03a6。同一转换库前 10000 条（276257519 B 原始 JSON，含 29.948 MB 单条记录）使用新原生 push 调用链再次导入。仍为一个 SQLite 冷工件，运行目录 34407179 B，恢复与只读视图共享不可变正文 inode；三目录合计独立分配 34553856 B，逻辑文件长度相加不能代表实际重复占盘。所有导入记录恢复后逐值相等。

原生接入后导入由 67.634 s 降至 4.622 s，其中纯 JSON 编码由 63.947 s 降至 1.139 s，压缩 0.561 s，读取旧分块并解码输入 2.674 s，SQL 与控制 0.236 s，显式文件同步 0.0127 s。编码计时在 push 回调中扣除下游压缩与 SQL 时间，没有把流式回调的总时间冒称编码时间。该独立进程总墙钟 11.940 s、CPU 11.928 s，包含 7.275 s 的计时外全值验证；绝对 RSS 高峰 360034304 B，前一 Python 编码路线 357695488 B。高峰包含单条输入解析，未将其归零或宣称固定总内存。

完整点发布 1.08 ms、恢复 4.31 ms、只读准备 3.49 ms、100 页读取 22.62 ms。显式同步计数沿旧合同记录 Python 层调用，不包含 SQLite 内部同步系统调用；页缓存未清空，单次实测不构成稳定时延分布。原始结果为 cold-datasets-native-real-subset-20261004.json，执行代码为当次 core_next_cold_datasets_probe.py 与原生 push 产品候选；服务器隔离副本位于 /tmp/society0-core-next-20261004/cold-datasets-native-source，数据试验目录由 TemporaryDirectory 清理。该对照支持删除 Python 逐原子编码循环，完整大根空间与运行对照仍需单独验收。
