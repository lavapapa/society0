# 公共可寻址随机数

本包通过 N-API 调用官方 Random123 Philox；Core 的装配入口是 `@society0/core/random`。源身份及三条款许可见 [SOURCE](<vendor/SOURCE.json>) 和 [官方 LICENSE](<vendor/random123-726a093cd9a73f3ec3c8d7a70ff10ed8efec8d13/LICENSE>)。

## 一、合同

[公开类型](<src/index.d.ts>)定义同步 `at(seed, address)`。Core 的 `RandomStreams` 注入 `random`；配置和算法版本属于结果相关的 new-run 设置。

`index` 是范围 `[0, 2^64-1]` 的规范十进制字符串（零写作 `0`，其余无前导零）。命名字段和 seed 接受所有良构 Unicode 字符串，包括空串、NUL；孤立 UTF-16 surrogate 明确拒绝。字符串保持原样，不作 Unicode 归一化。调用返回 `uint32` 和 `[0,1)` 的 `uniform`。

## 二、映射

`philox4x32-10/v1` 固定整个地址与采样合同。`length-prefixed-utf8-seed-seq/v1` 编码如下：

1. 域前缀为 UTF-8 `society0/random/address/v1` 后接一个零字节。
2. 顺序写入 seed、run、actor、operation、stream；每个字段先写八字节无符号 little-endian UTF-8 字节长度，再写 UTF-8 内容。
3. 每个编码字节分别作为一个非负整数交给标准 C++ `std::seed_seq`，`generate` 恰好四个 uint32。输出前两个为 Philox key，后两个为 counter 的 word2/word3。
4. counter word0 是 index 低32位，word1 是高32位。调用官方 `r123::Philox4x32_R<10>`。
5. 输出 word0 为 uint32；uniform 为 `((word0 >> 5) * 2^26 + (word1 >> 6)) / 2^53`，位移按无符号整数解释。

长度前缀消除字段拼接歧义。无限命名空间被映射到有限 key/prefix 空间，存在碰撞可能；输出随机值本身也可重复。uint32 与 uniform 使用同一组输出字，消费者需要独立随机用途时应使用不同 stream/index。run 是地址组成部分；分叉 seed 策略由后续运行分支协议决定。

## 三、重建

归档、完整官方头文件、原始测试向量和 LICENSE 随包分发。Node 24、Node C API headers、Python 3 和 C++17 编译器就绪后，在源码仓库的包目录执行：

```sh
python3 -B build.py --out-dir ../../.build/native-work/random-native
```

Node headers 默认来自当前 Node 安装的 `include/node`，可显式设置 `NODE_INCLUDE_DIR`。输出目录位于源包之外，包含 `build/random_native.node` 和 `NATIVE-BUILD.json`。构建记录声明实际编译器、Node版本与输出清单，原始来源声明保持不变。可消费包由仓库根构建器装配，参考产物保留用于直接字节校验。重建无需网络。发行原生二进制的平台以实际构建环境为准，其他平台须本地重建验证。
