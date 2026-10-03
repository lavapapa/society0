# 原生 JSON 流编码

复杂记录的原子级 Python 编码已成为冷数据导入主要耗时。该阶段采用成熟 python-rapidjson 的同步流式 dump，统一正文写入接口；压缩和完整步骤发布继续沿既有资源与恢复合同。

## 一、接口

`write_json(value, sink)` 将实际 JSON 字节推送到同步 sink，每次至多 64 KiB。使用严格字符串键、列表与字典，禁用字节自动转换、任意迭代器及非有限数字；任意大整数保留，浮点与文本按 JSON 值恢复。原生库编码的指数、转义等词法可以改变，所有范围偏移对应封存时实际字节，不二次重写为旧编码器样式。

`Writer.write_json_chunks(value, emit)` 在已有 writer 作用域调用共享 ChunkEncoder，emit 接收原文长度与 zlib 块，并同步写 SQL。短值保持同步压缩；大值用既有 store 懒创建有限线程池。前缀和在途原始字节预算保持原合同，原生编码与 emit 在规范线程，工作线程只压缩不可变 bytes。编码错误、emit 错误与中断都先排空已提交任务，再让事务回滚。

Thread、Memory、ResourceCalls、Results 迁移到同一 push 接口，冷 Dataset 把原始 push 字节送到已有分组缓冲。删除原有递归编码和 generator 外形，实验基线按冻结代码身份保留。调用方不在编码期间跨 await 或向其他任务发布可变输入。

## 二、内存

输出缓冲与压缩队列有固定边界；输入对象、原生 JSON 递归栈和字符串 UTF8 缓存另计。python-rapidjson 使用 Python 的 Unicode UTF8 缓存，首次编码非 ASCII 字符串可能增加该字符串完整 UTF8 表示，并与输入对象共同存活。这个边界已由源码和 sizeof/生命周期实验确认，不以 64 KiB 输出块冒称整个调用额外内存上限。巨原文可由 Document 或 Dataset 范围引用提供；显式完整值读取仍承担单值驻留。

## 三、验收

先新增入口失败用例，再验证全部 JSON 值与原始文本、超大整数、浮点、负零、Unicode 跨块、非法键、循环和非有限值。保留有限并发队列、中断时 drain、emit 部分失败事务回滚、关闭后拒绝及恢复逐值测试。单值 Unicode 缓存与输入解析高水位分别披露；真实导入分列源解码、JSON 编码、压缩、SQL 和耐久成本，验证全部消费者后再进行全根对照。

## 四、失败边界

RapidJSON 1.25 的二进制 `dump` 接口符合官方文件类 sink 约定。当前上游 `PyWriteStreamWrapper::Flush` 保留 Python 异常标志，单个字符串的后续 `Put` 仍可能继续调用 sink；因此 SQL 或压缩错误须由薄 sink 保留首个异常，随后停止 emit 副作用，原生遍历返回后抛回原异常。原生编码自身失败同样终止规范事务。该处理不承诺即时取消：输入余下部分仍可能遍历并为 Unicode 字符串建立 UTF8 缓存，现有原生压缩任务在退出时收束。

对应依据为 [官方 dump 文件接口](https://python-rapidjson.readthedocs.io/en/latest/dump.html) 与 [上游写入包装实现](https://github.com/python-rapidjson/python-rapidjson/blob/master/rapidjson.cpp#L317)。隔离复现与测量保存在 `research/core-next/native-json-sink-probe-20261004.py` 及同名 JSON；采用版本的产品回归覆盖原始异常身份、首次错误后零新增写入、事务回滚和工作线程收束。
