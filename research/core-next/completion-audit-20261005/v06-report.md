# 同源资料与主体行动对照

本轮以同一份十二条报价、主体任务及117字节报告核对旧输入能力与当前动态行动/VFS路径，实际指定模型完成资料获取、复算和提交。旧结构化接口与当前文件接口都完整取得原文并提交正确的12条、546元及带源句号的“原文校验成功。”。历史World完整输入组提交504元，按共同正确性标准判失败。此次成本记录没有显示当前文件接口在该确定题上节省调用或输入；各组交互合同和单次服务等待也限制了成本差异的归因。

## 一、方法

同源资料从当前产品`ab6469d`的`real-final-05`完成工件权威SQL表读取，先复验既有真实oracle，导出[v06-fixture.json](v06-fixture.json)。数据和正确提交均保持冻结：报价id按1至12顺序、amount为id乘7、总额546，报告正文完整117字节，短语保留原句号。各真实组使用SiliconFlow Qwen/Qwen3.8-27B、temperature=0、关闭推理与并行工具调用，保持20轮、2次领域行动、单请求60秒及1024输出token，自动记忆和嵌入未启用。凭据从隐藏TTY读取，未保存到工件。

有效结构化旧组`v06-structured-02`冻结`edabcad3e88ba7b7b92c68dcfa5c22bf6f2e304a`及其uv.lock，直接使用该提交原真实测试任务、装配工厂、SQLInformation、LLMDriver、workspace和bash/jq。它通过data_list、data_query、data_read读取资料，已具有动态action_find、action_describe、action_invoke。当前组`v06-current-01`冻结产品`ab6469d`，沿当前真实测试的文件入口、grep、bash data query和jq执行同一核对目标。两个版本都保留各自已有的完整分析能力；必要的路径及入口措辞按版本接口明确给出。

历史固定工具集组`v06-world-01`使用`96b1f3b`实际World、ActionSet和execute_action_loop，把同源完整原文直接放入模型输入，再执行固定catalog_submit。该版没有本次Agent VFS/shell工作区；其域工具也计入行动预算，因此该组采用历史完整输入合同。它检验原文确实进入请求、模型判断和固定行动提交，不能与文件发现路径解释为同条件计算能力对照。旧World固定openai 2.53.0，结构化旧组固定openai 3.24.0和Pydantic AI 2.54.0，当前组的公开合同随原工件保存。

## 二、结果

旧结构化组逐页读取四页，每页limit=3，total恒为12，透传游标至结束，十二条id与amount逐值相同。主体按expected_revision与字节offset分两次size=64读取并拼合117字节正文，找到所属actor Ref，发现和描述行动后唯一提交12/546/原文校验成功。第一次jq表达式出现“Cannot iterate over number”，exit_code=5；主体下一轮自主改正为对完整报价数组求和，exit_code=0输出精确JSON，再提交正确结果。独立判定见[v06-structured-02/v06-independent-result.json](v06-structured-02/v06-independent-result.json)。

当前文件组从共享目录发现价格数据、读取数据集schema，通过bash data query完整读取同四页报价，使用jq输出12/546；grep找到核对短语，原文read按64字节和revision续读，完整拼合相同117字节正文，再取得actor Ref、发现和描述提交行动，唯一实际提交12/546/原文校验成功。原真实测试与独立完整正文复核均通过，见[v06-current-independent.json](v06-current-independent.json)。原real-final-05成功效果工件继续保留，本次单场景current组额外补足可信计量。

World组的唯一真实请求中包含全部原始报价、actor任务和完整报告，实际固定工具提交12/504/原文校验成功。原文可达与行动执行成立，数值判断失败，原oracle失败保持不变，见[v06-world-01/v06-independent-result.json](v06-world-01/v06-independent-result.json)。这条历史完整输入样例及缺少同等计算工具的条件，均不足以推出新路径的一般判断优越性。

## 三、成本

有效结构化旧组有15次物理请求和15个完整响应，报告输入53,693、输出1,188、缓存读取34,304 token；提供方等待55.044秒，激活与任务墙钟约55.3秒。当前文件组有19次物理请求和19个完整响应，报告输入95,482、输出1,181、缓存读取46,336 token；提供方等待144.698秒，激活145.097秒，pytest场景145.645秒。两组无提供方错误，缓存写入未知。结构化旧组的两次jq尝试成本完整计入。

World组一次完整请求，报告输入668、输出55 token、缓存读取零，提供方耗时4.800秒，结果误算。其低成本来自完整材料直接输入与单次提交的合同，并未完成共同的正确判断，不能作为成功路径的效率收益。

两有效资料发现组均通过SDK现成openai_continuous_usage_stats处理端点累计流用量。此前real-final-05输入、输出及cache聚合存在累计相加问题，持久化记录缺少HTTP原终态流chunk，无法离线恢复真实token值；本报告没有拿失真数字比较。current单场景保持原行为预算，修正计量设置后新增真实工件；原成功、失败和计量缺陷记录全部保留。对应配置、正确摘要和独立复核在v06-current-01及本目录，修正范围还见[用量纠正记录](../filesystem-implementation-20261005/usage-correction.md)。

该样例当前文件合同多出schema读取、grep及文件Ref获取等实际步骤；模型服务等待发生在不同调用时段。本轮报告真实全任务成本，没有将19对15次调用或token差异解释为统一接口的因果效果，也没有推算价格。原文、全部报价、行动自主性、两次行动预算和完整Thread都保持可追溯。

## 四、验收

早期structured-01省略冻结旧测试已有workspace/jq，完整资料读到后误算528。该组保留为设计缺陷诊断，恢复原能力和原任务合同后的02组承担有效对照。World组504误算按失败保留。结构化02的冻结测试仍以第一条shell stdout为oracle，首条空stdout来自jq错误，后续成功纠错也触发原断言；原exit1保留，独立验收核对全部报价、完整原文、正确jq结果与最终唯一SQL提交后判定业务完成。

现行真实测试曾选第一条非空且含count的stdout，仍会误判“先错误计算、后自主纠正”。本轮仅修正测试oracle，要求存在exit_code=0且精确输出12/546的复算；最终唯一实际提交、四页原值与游标、源短语条件不变。先有确定性红测试，再验证自主纠错通过、仅失败或错误结果被拒绝。专属oracle与相关LLM workspace/shell组25项通过，红绿记录保留为v06-oracle-red.txt、v06-oracle-green.txt、v06-oracle-related.txt；小型[v06-oracle-fixture.json](v06-oracle-fixture.json)使测试不依赖完整运行数据库。

本轮关闭V06的有限真实同题信息与行动对照门：旧结构化和当前文件路径能够完整获取相同资料并完成正确任务，历史直接输入组的判断失败有实际证据，成本口径可信且失败没有被隐藏。样本数量、任务规模、版本接口、SDK及提供方时段限制了推断；目录化资料与动态行动的长期经营效果、任意资料负载及统计优越性仍需独立研究，不能由本题外推。
