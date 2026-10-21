# 比赛版三模型统一推理接口 v1

当前已实际跑通三个真实权重，完成命令行、统一 JSON 输出、证据协同和权重目录打包。HTTP 服务和前端联调还没有完成；队友可以先通过子进程调用或直接导入 `competition_infer.run` 接入。这不是重新训练三个模型。

## 固定比赛候选

`configs/competition_models_v1.json` 保存权重/归一化文件路径和 SHA-256。推理先逐文件校验，缺失或变更则报错，不临时下载或回退其他模型。

| 任务 | 模型 | 输入 | 输出 |
| --- | --- | --- | --- |
| timeseries | DC 八通道 RNN，15 分钟模型，固定早期训练折权重 | 至少 30 行、每分钟一行的原物理量 CSV；两个逆变器的电气量及一致比值 | 窗口分数、异常/正常/不确定、质量、阈值 |
| classification | ImageNet ConvNeXt-Tiny，PVF-10 十类微调 | 组件 RGB 热像裁图；先缩放到 112，再按模型输入到 224 | 十类概率、预测类、复核决策 |
| detection | YOLO26s 切图权重，640 推理 | 640×512 巡检 RGB 热像，四个重叠块 | 原图像素坐标框、分数、候选数 |

候选用于比赛可运行交付，没有按这三次样例推理重新选型。时序权重只使用此前某个早期训练折，归一化和阈值必须配套，不能换成全数据统计。分类权重曾用重复查看的开发验证区选种子；0.7 分类最大分数、0.25 检测阈值是固定演示策略，没有新增校准结论。

时序当前为保守质量策略，沿用 14 通道/30 分钟源窗口检查，再抽八通道最近 15 分钟。因此缺少任何源通道都可能拒判，不能宣称已支持任意电站数据。原始字段见 `prepare_reunion.FEATURES`：`faulted/control` 各自的 Pg/Ia/Ig/Va/Vg/Fg，加 `power_ratio`、`dc_current_ratio`；量纲须与 La Réunion 数据一致。两比值必须分别等于 Pg/(control Pg+50)、Ia/(control Ia+.2)。缺测、低对照功率、时间间断不能被静默填成健康。

## 运行

从仓库根目录执行。普通 Python 需安装 torch、torchvision、numpy、pandas、scikit-learn；检测还需 Ultralytics。现有本机 `.venv-detect/bin/python` 可用于检测。CLI 默认 CPU 是**推理**，不是 CPU 训练；分类另已验证 MPS。CUDA 可通过 `--device cuda` 使用；当前统一检测入口明确不支持 MPS，避免库 NMS 超时丢预测。

```bash
python3 src/competition_infer.py --task timeseries \
 --input /path/to/telemetry.csv --output reports/ts_result.json \
 --plant-id actual-plant --asset-id verified-inverter --observation-id window-source-id \
 --data-kind site_observation --device cpu

python3 src/competition_infer.py --task classification \
 --input /path/to/module.png --output reports/class_result.json \
 --plant-id actual-plant --asset-id verified-asset --observation-id original-photo-id \
 --observed-at 2026-10-21T10:00:00+08:00 --data-kind site_observation --device cpu

.venv-detect/bin/python src/competition_infer.py --task detection \
 --input /path/to/inspection.png --output reports/detect_result.json \
 --plant-id actual-plant --asset-id verified-asset --observation-id original-photo-id \
 --observed-at 2026-10-21T10:00:00+08:00 --data-kind site_observation --device cpu
```

上述设备、时间均需替换成真实采集信息；分类与检测同一原图须共用 observation_id。检测整图的资产归属不能未经确认就映射到分类的单组件。

CLI 拒绝覆盖已有结果。调用错误/损坏文件/权重不匹配返回非零退出码，不能将错误页面当正常诊断。输入不满足已实现的质量规则时输出 blocked、uncertain，而非“正常”。当前图像质量仅检查有效图像、常量空间信息及检测输入尺寸，并记录梯度代理值；**尚无经验证的模糊检测阈值**。

## 给前后端的 JSON 字段

顶层 `schema_version=competition-inference-v1`，包含 task、bundle_version、bundle_sha256、input_sha256、device、elapsed_seconds 和 `evidence` 列表。CSV 可产生多条窗口记录。

每条 evidence 可直接送入 `competition_evidence`，包含身份、采集时刻、源观测 ID、权重哈希、阈值版本、decision、quality、quality_reason、data_kind、原模型 score（如有）、input_sha256。详情位于：

- 时序：`model_output.context_minutes / threshold / features`。
- 分类：`model_output.predicted_class / class_probabilities`。
- 检测：`model_output.detections`，每项为 `bbox_xyxy=[x1,y1,x2,y2] / score / class`；坐标基于原 640×512 图像。
- `quality_details`：质量依据和观测比例/尺寸信息。

分类最大分数低于 0.7 返回 uncertain。检测没有框也返回 uncertain，不能把“没有热斑框”升级为整体健康。各任务 score 不可直接平均。`elapsed_seconds` 包含模型初始化等，仅用于记录本次运行，不能作为 GPU 纯模型基准速度。

## 接协同模块

输入既可以是旧的证据列表，也可以直接是一个或多个推理报告：

```bash
python3 src/competition_evidence.py \
 --input reports/ts_result.json reports/class_result.json reports/detect_result.json \
 --output reports/review_result.json --plant-id actual-plant --asset-id verified-asset \
 --as-of 2026-10-21T10:10:00+08:00
```

必须匹配设备与时段；不同公开数据源不会因为都输出异常就成为联合故障证据。实际 smoke 测试把三个公开数据源报告输入协同入口后，只采用目标资产的分类证据，另外两个被 `different_asset` 排除，结果 single_source_review，符合预期。

## 可复现的本机演示

```bash
python3 src/prepare_competition_demo_inputs.py --output reports/competition_real_inputs_new
INPUTS=reports/competition_real_inputs_new bash scripts/run_competition_smoke.sh reports/competition_inference_new
bash scripts/demo_competition_evidence.sh reports/competition_policy_new
```

准备程序只读现有训练/开发验证数据。时序选用早期训练折重构窗口；图像从各自开发验证源取样。它们证明功能可运行，**不提供新的模型泛化分数**。图像精确采集时刻未知，smoke 脚本分配的时刻明确 `synthetic_demo`；时序、分类、检测分属不同资产，不人为伪装配对。模拟业务场景另在 `examples/competition`。

这轮实际输出：时序一条 anomaly、分类预测 hot cell、检测 7 个候选框；三个模型均使用配置中真实权重，未下载新权重。9 项比赛模块相关测试通过。

## 权重搬到队友机器

```bash
python3 src/package_competition_models.py --output reports/competition_models_portable_new
```

已经实际打包 `reports/competition_models_portable_v1/`，含三个权重及一个归一化文件，复制后逐一校验 SHA-256。将整个目录传给队友，代码仍从本 Git 仓库取得；推理添加：

```bash
--bundle /path/to/portable/competition_models_v1.json --root /path/to/portable
```

可搬移配置已用真实时序权重运行通过；其余两条原路径真实推理均通过，portable 文件与原文件哈希一致。权重不提交 Git，源数据也不打进权重包。比赛使用与展示仍保留数据来源说明。
