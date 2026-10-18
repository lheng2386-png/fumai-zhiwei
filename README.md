# 伏脉智维 Fumai Zhiwei

面向光伏场站的多源设备智能诊断与数字化运维研究项目。

研究重点是结合工况归一化、同群设备对比与功率残差，形成具有证据解释和人工反馈的运维流程。当前仓库为算法基线阶段，完整平台与研究方法尚在开发。

## 已实现

- 本地时序数据审计，包括字段、缺失、重复键和时间范围。
- 每台逆变器的梯度提升树功率参考模型与持续低发电候选事件输出。
- 正常、组串短路、模拟退化、组串开路和遮挡五分类基线。
- 基于 RDTools 公开预训练模型的削顶识别调用。
- 模型保存、CSV 推理和输入完整性检查。
- 同群设备比值对比、连续候选事件合并及证据导出。
- 离线事件筛选报告和保留历史的本地人工复核记录。

## 安装与运行

建议 Python 3.12，先创建虚拟环境：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-lock.txt
```

macOS 的 XGBoost 需要 OpenMP，可通过 `brew install libomp` 安装。

自行准备已获授权的数据，将电气、辐照、环境 CSV 及元数据放入 `data/input/`，或使用 `--data-dir` 指定目录。当前功率参考基线适配 PVDAQ 2107 文件模式，不是任意电站 CSV 的通用导入器。

```bash
python src/fetch_public_fault_data.py
python src/run_baselines.py --data-dir data/input
python src/check_pretrained_clipping.py --data-dir data/input
python src/predict_fault.py reports/fault_inference_sample.csv --output reports/fault_predictions.csv
```

## 运维事件回放与复核

完成基线训练后，可直接使用保存的功率模型回放 2023 年留出数据：

```bash
python src/replay.py --data-dir data/input --start 2023-01-01 --end 2024-01-01
```

打开 `reports/replay/index.html` 筛选候选事件；逐点证据、事件和输入/模型 SHA256 分别保存在 `evidence.csv`、`events.csv` 和 `summary.json`。暂定同群仅适用于 2107 场站，其他场站需要重新适配和验证。时间沿用场站本地墙上时钟 PST8PDT，结束日期不包含在回放区间内。

用报告中的真实事件 ID 保存复核；状态可为 `confirmed_fault`、`expected_operation`、`data_issue` 或 `needs_investigation`。确认故障应记录现场、工单等实际证据。

```bash
python src/review.py --event-id EVENT_ID --status needs_investigation --note "记录需要核实的现象"
python src/replay.py --data-dir data/input --reviews-db reports/reviews.sqlite
python -m unittest discover -s tests -v
```

复核仅写本地数据库，不会自动改变训练标签。回放覆盖生成的报告文件，人工复核数据库保留。规则、回放结果与限制见 [研发记录](DEVELOPMENT.md)。

## 评估范围

功率参考按 2022 年前训练、2022 年定残差阈值、2023 年留出测试。原始运行数据未提供确认故障标签，候选异常数量不能解释为诊断准确率。同一时刻气象条件下的功率参考不等于未来发电量预测。

DC 侧五分类使用 Costa/Lazzaretti 公开双组串数据，原始有序行前 80% 训练、后 20% 测试，边界两侧各隔离 100 行，每 10 行抽样一次。本地第一轮准确率 96.01%、宏平均 F1 92.04%。成绩仅代表该单电站数据流后段测试，尚未验证跨电站或独立新故障事件泛化，也不是逆变器内部故障或故障提前量预测。

## 研究方向

1. 多源运行数据的质量治理与工况识别。
2. 同群设备对比与功率残差联合异常筛查。
3. 设备故障辨识、拒识与证据解释。
4. 事件复核、工单处置及效果验证。
5. 在获得适用图像数据后接入组件巡检结果。

## 数据与开源参考

- [Costa/Lazzaretti 作者数据](https://github.com/clayton-h-costa/pv_fault_dataset)，使用时保留作者 Apache-2.0 许可并引用[原始论文](https://doi.org/10.3390/s20174688)。
- [RDTools](https://github.com/NatLabRockies/rdtools)，用于公开预训练削顶检测组件。
- [EnergiaMind](https://github.com/dunglt69/solar-anomaly-detection)，作为监控、模型服务和告警工单结构参考；不将其代码或模型声明为本项目自主成果。

本仓库只包含研究代码和说明，不包含团队原始数据、第三方模型权重、账号配置或本机审计日志。数据和权重的许可分别遵循其原始发布方规定。
