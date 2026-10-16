# JSON 编码与分块写入

插件正文写入使用 `society0.kernel.storage.Writer.write_json_chunks`，Thread、Memory、Results 与冷数据集共享这套原生 JSON/zstd 编码。应用层通常提交 JSON 值，由相应服务保存；只有自定义正文格式才直接调用这个入口，输入对象在编码期间要保持稳定。

## 调用入口

同步编码在规范写者线程完成，回调即时消费独立压缩帧。

<!-- api: society0.kernel.storage.Writer.write_json_chunks -->

`writer.write_json_chunks(value, emit)` 中 emit 接受 `(raw_bytes, compressed_bytes)`：前项为该帧原文长度整数，后项为标准独立 zstd 帧 bytes。emit 在当前同步事务内执行，可写入领域的块表；返回值不用于异步调度。编码期间保持输入对象稳定，不跨 await 交付仍会修改的对象。

公开写入服务已处理记录定位和块表。自定义正文格式使用此低层入口时，需自行记录块顺序、原文长度与所属记录，读取时依据同一布局还原。实现入口为 [write_json](<https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/_json_chunks.py#L9>) 和 [ChunkWriter](<https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/_json_chunks.py#L31>)；下划线模块属于内部实现，不宜由业务代码承担其生命周期。

## 接受的输入与原文

编码接受 JSON 值，保留数值、字符串内容和列表顺序。

允许 null、布尔、整数、有限浮点、字符串、列表及字符串键字典。任意大整数保留，bytes 自动转换、任意迭代器及非有限数被禁用；循环结构和非法类型使编码失败。表行生成器可交给 Results 或 Datasets 逐条消费，每条业务值本身仍须满足上面的 JSON 取值要求。

原生编码器可以采用不同的指数或转义词法。字节偏移对应实际封存的 UTF-8 JSON 字节；范围消费者按保存时的原文继续读取，避免重新编码后沿用旧偏移。读取完整 JSON 值与读取原文字节返回形状不同，业务应明确选择。

## 失败处理与内存开销

每块最多 64 KiB 原文，这一界限约束帧缓冲。

输入 Python 对象、JSON 递归栈、Unicode UTF-8 缓存、压缩器与 SQLite/Session 内存另计。首次编码非 ASCII 大字符串可能建立完整 UTF-8 缓存；请求完整值的调用者仍承担整个值的驻留成本。同步编码占用当前线程，适合按规范事务明确预算。

emit 首次异常被保留，后续 emit 副作用停止；原生遍历结束后重新抛出原异常，使所属事务回滚。此行为不保证在首次错误处立即停止遍历输入，也不提供任意外部副作用的回滚。规范 Writer 回调结束后借用失效，后续编码调用抛 StorageError。

大正文读取参见 [SQL 信息](<information.md>)和[冷数据集](<cold-datasets.md>)，使用流程见[数据集指南](<../guide/datasets.md>)。
