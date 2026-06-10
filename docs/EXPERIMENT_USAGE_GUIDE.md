# 🧪 控制变量实验运行指南

## 📋 可用的运行方式

### 1. 前台运行（交互式）
```bash
./run_intervention_comparison.sh
```
- ✅ 实时查看彩色输出
- ✅ 可以随时用 Ctrl+C 中断
- ❌ 需要保持终端连接

### 2. 后台运行（推荐用于长时间实验）
```bash
# 使用nohup后台运行
nohup ./run_intervention_comparison.sh > experiment_output.log 2>&1 &

# 记录进程ID以便后续管理
echo $! > experiment.pid
```

### 3. 后台运行with进度监控
```bash
# 启动后台实验
nohup ./run_intervention_comparison.sh &
EXPERIMENT_PID=$!
echo $EXPERIMENT_PID > experiment.pid

# 实时监控日志
tail -f experiment_log_*.txt

# 检查进度（在另一个终端）
cat experiment_progress_*.json | jq '.'
```

## 📊 监控和管理

### 查看运行状态
```bash
# 检查进程是否还在运行
ps aux | grep run_intervention_comparison

# 查看最新日志
tail -20 experiment_log_*.txt

# 检查进度JSON
ls -la experiment_progress_*.json
cat experiment_progress_*.json
```

### 中断实验
```bash
# 优雅终止（推荐）
kill $(cat experiment.pid)

# 强制终止（谨慎使用）
kill -9 $(cat experiment.pid)
```

## 📁 输出文件说明

### 实验过程中生成的文件
- `experiment_log_YYYYMMDD_HHMMSS.txt` - 完整的执行日志
- `experiment_progress_YYYYMMDD_HHMMSS.json` - 进度追踪文件
- `experiment.pid` - 进程ID文件（手动创建）

### 实验完成后的结果
```
experiment_results_YYYYMMDD_HHMMSS/
├── output_ai_model/          # AI标记实验的完整输出
├── output_moderator/         # 管理员标记实验的完整输出
├── output_user_report/       # 用户举报实验的完整输出
├── report_ai_model.md        # AI标记实验分析报告
├── report_moderator.md       # 管理员标记实验分析报告
├── report_user_report.md     # 用户举报实验分析报告
├── FINAL_COMPARISON_REPORT.md # 三种干预方式的对比分析
└── experiment_*_metadata.json # 各实验的元数据
```

## ⚡ 快速开始

### 推荐的后台运行命令
```bash
# 一键启动后台实验
nohup ./run_intervention_comparison.sh > experiment_output.log 2>&1 &
echo $! > experiment.pid
echo "🚀 实验已在后台启动，PID: $(cat experiment.pid)"
echo "📋 查看日志: tail -f experiment_log_*.txt"
echo "📊 检查进度: cat experiment_progress_*.json"
```

### 监控命令
```bash
# 实时查看进度
watch -n 30 'cat experiment_progress_*.json 2>/dev/null || echo "进度文件未找到"'

# 查看实验输出目录
watch -n 60 'ls -la studies/misinformation_v2/output/ 2>/dev/null || echo "实验尚未开始"'
```

## 💡 使用建议

1. **测试运行**: 先用1步和少量智能体测试
2. **正式实验**: 增加步数(5-10步)和智能体数量以获得有意义的数据
3. **资源监控**: 长时间实验时监控CPU和内存使用
4. **结果分析**: 重点关注三种干预方式对信任度指标的不同影响