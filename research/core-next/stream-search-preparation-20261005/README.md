# 流式搜索准备试验

本次研究为主体的 grep 文件入口选择成熟搜索组件。基线为 Society0 `55d1913ca2e5cd5be84dbab0004d29580954c827`，试验于 2026-10-05 在本机运行。结论是优先将 ripgrep 的 `grep-searcher` 与 `grep-regex` 接入已有原生扩展，以每个逻辑文档的原文字节范围构成 Reader；宿主 `rg` stdin 可作独立对照。这里保存隔离试验及选型依据，产品代码、产品依赖锁和正式工具合同保持原状。

## 一、依据

`Searcher::search_reader` 接受标准 `Read`，默认逐行增量搜索，结果交给 Sink。一个 Searcher 可重复处理不同文档，原文块属于同一 Reader 的连续字节流。开启真正跨行搜索可能要求读入完整输入；本方案采用普通逐行语义。[官方 Searcher 文档](https://docs.rs/grep-searcher/0.1.17/grep_searcher/struct.Searcher.html#method.search_reader)。

`RegexMatcherBuilder::line_terminator(Some(b'\n'))` 让成熟正则组件保证匹配不跨换行，显式换行模式会构建失败；中文字符、量词、分组和块边界由上游处理。本试验模式为 `关(?:键)词`，未实现跨块正则算法。[官方 RegexMatcherBuilder 文档](https://docs.rs/grep-regex/0.1.14/grep_regex/struct.RegexMatcherBuilder.html#method.line_terminator)。

逐行流式搜索的内存由最长行及上下文决定。`heap_limit` 是搜索器近似堆预算，超出时明确返回错误。它不覆盖所有正则、调用方和运行时内存。[官方预算说明](https://docs.rs/grep-searcher/0.1.17/grep_searcher/struct.SearcherBuilder.html#method.heap_limit)。已下载的 0.1.17 源码中，`LineBuffer` 初始为 65,536 字节，填满且没有完整行时 `ensure_capacity` 追加当前长度两倍，因而缓冲长度按三倍增长。该实现说明长行成本以及实际增长阶梯。[官方版本源码](https://docs.rs/grep-searcher/0.1.17/src/grep_searcher/line_buffer.rs.html)。

## 二、结果

独立 `Cargo.toml` 固定 `grep-searcher=0.1.17`、`grep-regex=0.1.14`，其余依赖由本目录 `Cargo.lock` 固定。运行 `cargo run --release > rust-results.jsonl` 可复现 Rust 试验，运行 `python3 rg_probe.py` 可复现宿主对照。数据由 Reader 或生成器即时提供，最大输入 4 MiB，未创建大正文文件，未调用模型服务。断言随试验执行。

Rust Reader 每次最多返回 2 字节时，中文 UTF-8 字符与关键词在多次读取间拆开，仍得到一个正确命中。在 64 KiB 块边界拆开关键词也命中。复用同一 Searcher 分别搜索 `关键`、`词`、`关键词` 三个逻辑文档，命中数为 0、0、1；搜索调用间保持文档边界。显式换行正则按逐行合同被拒绝。

1 MiB 单行的最大分配请求为 1,769,472 字节，搜索阶段峰值堆增量为 1,777,680 字节。4 MiB、每行 128 字节的输入峰值堆增量为 73,744 字节，最大分配为 65,536 字节。包含四条 1 MiB 长行的 4 MiB 输入维持与单条长行相同的峰值。给长行设 64 KiB 搜索器堆预算时，在取得 65,536 字节后返回 `configured allocation limit (65536) exceeded`。因此内存成本随最长行增长，不能承诺任意单行长度下的固定内存搜索。

计数来自隔离单线程程序的 GlobalAlloc 请求统计，包含搜索器构建、搜索期正则缓存和 Sink；编译正则发生在计数前。统计记录已申请存活字节和最大申请大小，不代表进程 RSS、底层分配器内部容量或 realloc 内部的瞬时双份拷贝。原始数字见 `rust-results.jsonl`。提前停止 Sink 在首行命中后结束，实际已读取 65,536 字节，说明搜索器存在读前量。

本机宿主为 ripgrep 15.1.0。通过 stdin 分次写入的小中文用例和 1 MiB 长行均正确命中，三个独立子进程保留文档边界。第一次试验的 30 个小文档进程中位耗时约 5 毫秒，重跑数值保存在 `rg-results.json`；这是当前机器的小样本启动、管道和搜索合计，Rust 进程内循环还没有 Python 桥开销，二者不构成产品吞吐对照。`--only-matching --byte-offset --line-number` 将该长行结果缩至 18 字节，而 `--json` 输出约 1.049 MiB，包含整条匹配行。试验边写 stdin 边消费 stdout，避免两端管道互相阻塞；内核可能合并相邻写入，宿主试验未测量 rg 的底层 read 分块和进程峰值内存。

## 三、接入

已有 `native/society0-filesystem/src/lib.rs` 使用 PyO3、Tokio 和 `pyo3_async_runtimes::TaskLocals`，可将搜索入口加入同一扩展，复用 `CallbackFs::call` 中回到调用者 Python 事件循环的方式。`std::io::Read` 是同步接口，应在现有受限计算线程或原生阻塞线程执行搜索；每次按有限范围调用主体已绑定的 `Information.read(path, offset, size, expected_revision)`。创建 Python awaitable 和提取返回字节时短暂持有解释器锁，等待期间释放。保留现有 Python reader 的线程归属，底层 SQL 读取仍在拥有它的调用者线程执行。这个同步 Reader 与异步回调的组合尚未做完整桥接试验，取消与关闭必须在后续验收中覆盖。

现有 C capsule 导出整文件 `read_file`，本轮核查的 Bashkit 0.18.2 `interop/fs.rs` 没有范围读取或 SearchCapable 导出。原生 grep 即使走 `try_indexed_search`，仍会对候选调用 `fs.read_file`。因此顶层 grep 可直接调用新搜索入口；要让 bash 内 grep 具有相同合同，还需明确挂接同一服务的 builtin 或上游接口。继续经原始 capsule 执行现有 grep，无法取得本试验的范围成本。

每个逻辑文档单独调用 `search_reader`，一次目录搜索复用已编译 matcher 和 Searcher，避免逐文档创建进程。范围回调次数约随扫描字节数除以传输块长增长；默认搜索器会先读取少数字节再扩展读取，必要时可使用标准 `BufReader` 合并这些小请求。回调层应把单次上游请求限定为例如 64 KiB；搜索器为了长行扩大的 out buffer 不应直接变成同尺寸 Python 请求。传输仍有 Python bytes、Rust 拷贝与调度开销，不能因没有子进程就推断这一成本可忽略。

Sink 可取得命中行的原文字节偏移和行号，向现有结果 artifact 写入结果并返回有限预览与续读引用，避免先聚合全部命中正文。整个搜索的工作量仍由授权枚举文档数和实际扫描字节数决定。宿主方案多出每文档进程启动、完整扫描数据的管道传输和输出解析；stdio 本身只有一条输入流，把多个逻辑文档串接会改变文件边界。Rust 组件方案已有可复用的原生承载位置，故作为首选。

## 四、边界

原文范围读的上游必须具有范围成本。`SQLInformation.read` 的 DocumentSpec 使用 `read_blob`；DatasetSpec 当前先查询完整记录并 `json.dumps`，随后切片。对后者重复请求小块，会重复物化整条记录。本选型解决搜索边界和搜索缓冲，实际接入还要让这类逻辑文档获得一次生成后可范围读取的原文来源，或复用已有不可变正文引用。工作区的现有 read callback 同样整文件读取；私有巨文件也需要贯通原文范围入口。

结果分页可直接沿用结果 artifact 的字节范围。扫描预算需要区分完成、预算结束和错误，并携带文档身份与版本。提前停止时的 Reader offset 含读前量，不能直接拿作继续搜索游标；继续位置应落在已处理的完整行边界，并保留准确的原文偏移与行号。先完成搜索再分页读结果的路径最简单；若确需中途停止扫描，完整行边界、未消费缓冲和上下文行属于必须单独验收的条件。长行预算耗尽也应保留可范围读取的原文入口。

本轮已验证成熟组件的跨块中文、普通逐行正则、独立文档、长行缓冲、显式预算错误和提前停止。尚待产品接入验证的是 PyO3 异步回调及取消、结果 artifact 的增量写入、现有权限及版本边界、SQL 记录原文的物化方式，以及真实文档分布下的桥接耗时。现有证据足以确定首选组件，并给出后续实现的可验证边界。
