# 组件热斑目标检测：数据审计与首轮基线

这是**目标检测**任务：输入 640×512 的巡检红外整图，输出热斑边界框。它与低分辨率组件裁图的 12 类分类不是同一个模型。当前已完成数据审计、分组划分和 640 像素 YOLO 基线；仍不能用于工业告警。

## 数据来源和问题

[PV-HSD-2025 发布页](https://huggingface.co/datasets/MisakaMikoto128/PV-HSD-2025)提供 `PV-HSD-2025.zip`。固定版本 SHA `40748bc7c445cf9125f2cd25d115fa35bbeb17af`，下载归档 SHA-256 `3fac0ae8e3e345e9f3d1c45afb4251530f79bfda8d772aacd708799a32416c32`。ZIP CRC、图像与标签配对、全部 25,177 个单类 YOLO 框均已校验；没有发现无效坐标。原始归档含训练 2,808 张、验证 1,217 张。其中 `62_2.JPG`、`62_3.JPG` 是 **4000×3000 可见光照片**，没有热斑框；其余图像是 640×512。两张可见光照片从红外检测训练集中隔离，并在清洗清单中留痕。

发布方原始文件名形如 `0_1.JPG` / `0_2.JPG`，随包提供的原始路径映射可以还原 `check1/pack...`。按“原始目录＋数字前缀”暂作采集组，发现原始训练/验证共有 **745 个组**；原始验证图有 **1,108/1,217** 张与训练图共享组。没有发现跨集合完全同文件的 CRC+大小重复，但相邻帧可能高度相似。这个组定义仍需原始航次元数据确认，不能被解释为场站 ID。

`src/prepare_pv_hsd.py` 将原始两部分合并、隔离两张异模态图片后，以固定种子 2026、组 ID 的 SHA-256 哈希重新划分：

| 分区 | 图片 | 热斑框 | 空标签图片 | 采集代理组 |
| --- | ---: | ---: | ---: | ---: |
| 训练 | 2,868 | 19,077 | 129 | 873 |
| 验证 | 571 | 3,201 | 47 | 179 |
| 留出测试 | 584 | 2,899 | 49 | 184 |

三个分区组 ID 不重叠。严格的感知哈希和差分哈希联合筛查没有找到跨训练/验证或训练/测试的高相似度画面；这只是近重复启发式筛查。所有源路径都指向 `check1`，所以**仍是同一批采集数据**；留出测试只能检验这批数据中的分组泛化。空标签仅表示没有标注热斑，不等于已人工确认健康，且数量太少，工业误报率仍需要新的完整正常航次评估。

框非常小：训练集热斑框的中位宽/高只有约 **10×8 像素**，约 **54%** 的框短边小于 8 像素，约 **95%** 的框短边小于 16 像素；验证集比例相近。因此 320 像素试跑会显著缩小目标，不能用于架构选择。640 像素原尺度基线之后，应比较局部切图放大、小目标增强和较高推理分辨率，并同时记录边缘框丢失、额外推理时间与误报。原始热像分辨率只有 640×512，简单插值不会创造新的测量细节。

数据卡元信息、嵌入的 YOLO YAML 和文字说明给出不一致的许可信息；正文明确限制学术研究，商业使用需联系作者。当前实验按研究用途处理，不把该数据或模型权重投入商业生产。

## 框架筛选协议

首轮以公开预训练检测器微调：YOLO 作为工程基线，D-FINE 小/中型作为后续比较对象；两者固定相同组划分、输入分辨率和近似训练预算，先用验证集选择，再**一次**读取组留出测试。除了 mAP50-95，还要记录热斑漏检、每幅空标签图的假阳性及单图推理时间。因为空标签不等于真正常，假阳性指标仅作数据内参考；工业验收另需人工确认正常的独立场站整图。

本地准备命令：

```bash
python3 src/audit_pv_hsd.py --archive data/raw/pv_hsd_2025/PV-HSD-2025.zip --output data/raw/pv_hsd_2025/audit.json
PYTHONPATH=src python3 src/prepare_pv_hsd.py --archive data/raw/pv_hsd_2025/PV-HSD-2025.zip --output data/processed/pv_hsd_grouped_v2
```

`src/train_pv_hsd_yolo.py train` 只使用训练和验证分区；`test` 是独立显式命令，须待架构与权重冻结后使用。原始图、处理结果和权重不上传 GitHub。

CUDA 服务器上可运行 `PYTHON_BIN=/path/to/python bash scripts/run_pv_hsd_cuda.sh`。脚本先核对归档 SHA-256，再按固定规则准备分组数据，并以 COCO 预训练 YOLO26s 在 640 像素、batch 8、最多 80 轮下训练；早停耐心值为 20。训练元数据记录框架版本、预训练权重校验值、GPU 和耗时。留出测试不由该脚本执行。

D-FINE-S 对照使用[官方实现](https://github.com/Peterande/D-FINE)，固定代码版本 `956d1709314c2c6a4df6f34de232054578a7449f` 和官方 COCO 预训练权重 `dfine_s_coco.pth`（本地 SHA-256 `48a6c8cc43eb57186843f752e2e8461ddd3326e0d3c575e71e6e960844683e89`）。`src/convert_pv_hsd_coco.py` 仅把训练/验证标签转成 COCO 格式；`src/configure_pv_hsd_dfine.py` 按同一图片划分生成单卡 640 像素、batch 8、80 轮配置。在官方代码和权重放入 `research/D-FINE` 并安装其依赖后，运行 `PYTHON_BIN=/path/to/python bash scripts/run_pv_hsd_dfine_cuda.sh`。D-FINE 保留其官方 EMA，学习率按单卡小 batch 下调。COCO 官方排行榜分数不能替代本数据集上的实际对照；两模型的增强、损失和训练计划不同，80 轮也不是绝对等算力比较，后续还需看收敛曲线和相近 GPU 时长。

## 训练流程试跑

Apple M4 Pro 的 MPS 上用 COCO 预训练 YOLO26s、320 像素、batch 8 跑完 **1 个 epoch**，训练和验证共约 182 秒；验证 571 张图的 mAP50 为 **0.176**、mAP50-95 为 **0.0446**。这次流程试跑用的是尚未隔离两张可见光图的 v1 数据，因而不作为清洗版模型比较结果。它只证明数据加载、训练、保存和验证流程可执行。低分辨率单轮训练远未收敛，不能据此比较架构或宣称最终精度；正式比较将用 640 像素和足够训练轮次，并记录相同分组划分下的验证曲线。

## 清洗版 YOLO26s 分组留出结果

在清洗版、采集代理组不重叠的 `train/val/test = 2,868/571/584` 划分上，使用 COCO 预训练 YOLO26s、640 像素、batch 8、单张 RTX 3090、最多 80 轮和早停耐心 20 完成训练。训练耗时约 42 分钟；训练结束后冻结 `best.pt`，显式读取测试分区一次。

| 指标 | 分组留出测试 |
| --- | ---: |
| 图片 | 584 |
| 热斑框 | 2,899 |
| Precision | 0.7257 |
| Recall | 0.7071 |
| mAP50 | 0.7114 |
| mAP50-95 | 0.2912 |
| 推理速度 | 2.0 ms/image（3090，640 像素，不含数据采集） |

结果文件位于 GPU 服务器 `reports/pv_hsd_yolo26s_cuda_v2_test/one_shot_test.json`，权重为 `reports/pv_hsd_yolo26s_cuda_v2/weights/best.pt`。该测试仍来自一批采集数据，原始图像没有独立场站或完整正常航次；空标签图数量也不足以估计工业假阳性率。因此这是当前目标检测基线，不是生产验收结果。下一步比较 D-FINE-S、局部切图/高分辨率小目标方案，并记录每幅完整正常图的假阳性和现场复核结果。

## 小目标切图候选：数据和整图评估入口

为放大约 10×8 像素的热斑，`src/prepare_pv_hsd_tiles.py` 将每张 640×512 原图切成四张 384×320 的重叠图块，水平和垂直重叠均为 128 像素。输入到 640 像素训练时，热斑在模型输入中的尺寸约增至原来的 1.67 倍。重叠区的目标会重复标注；被图块边缘截断但可见至少 2×2 像素的框会裁剪后标注。切图只处理原始分组划分的训练和验证两部分，留出测试保持原样。

本地已生成 `data/processed/pv_hsd_tiles_384_v1/` 并逐框核对：训练 **11,472** 张图块、**32,034** 个图块框实例，验证 **2,284** 张图块、**5,369** 个图块框实例；原始训练 **19,077/19,077** 和验证 **3,201/3,201** 个热斑都至少完整落入一个图块，丢失 **0** 个。图块文件约 **794 MB**，不提交 Git。采集代理组沿用原始划分，不能因重复切图而重新随机分组。

候选训练入口为 `src/train_pv_hsd_tiled_yolo.py`，沿用 YOLO26s、640 输入、batch 8、最多 80 轮和相同预训练权重。训练时的图块验证 mAP **不能直接和整图基线 mAP 比较**。训练后使用 `src/evaluate_pv_hsd_tiled.py` 在原始 571 张验证整图上做四图块推理，将框映射回 640×512 坐标，过滤图块内部边缘的截断框，按整图 NMS 合并，再由 COCOeval 计算 mAP、召回、空标签图上的检测数和整图耗时。该脚本还支持 `--mode full`：以相同标注、置信度阈值、最终 NMS 和 COCOeval 重算原尺度基线，从而在验证集上进行同口径比较。旧基线的留出测试分数与这里的验证 COCO 分数属于不同划分和评估实现，不能直接相减。

```bash
PYTHONPATH=src python src/prepare_pv_hsd_tiles.py \
  --source data/processed/pv_hsd_grouped_v2 \
  --output data/processed/pv_hsd_tiles_384_v1
PYTHONPATH=src python src/train_pv_hsd_tiled_yolo.py \
  --source data/processed/pv_hsd_grouped_v2 \
  --tiles data/processed/pv_hsd_tiles_384_v1 \
  --output reports/pv_hsd_yolo26s_tiles_cuda_v1 --device cuda
PYTHONPATH=src python src/evaluate_pv_hsd_tiled.py \
  --source data/processed/pv_hsd_grouped_v2 \
  --checkpoint reports/pv_hsd_yolo26s_tiles_cuda_v1/weights/best.pt \
  --output reports/pv_hsd_yolo26s_tiles_full_val_v1.json --device cuda --mode tiled
PYTHONPATH=src python src/evaluate_pv_hsd_tiled.py \
  --source data/processed/pv_hsd_grouped_v2 \
  --checkpoint reports/pv_hsd_yolo26s_cuda_v2/weights/best.pt \
  --output reports/pv_hsd_yolo26s_full_val_coco_v1.json --device cuda --mode full
```

2026-10-19 重启后的单张 RTX 4090 可用。服务器已重新生成训练/验证 COCO 标注（**未生成测试标注**），基线权重在原始 571 张验证整图上通过上述统一 COCOeval 入口得到 **mAP50-95 0.3077、mAP50 0.7311、AR@100 0.4306**。该值与旧测试 mAP50-95 0.2912 属于不同划分，不能直接相减。验证集有 47 张空标签图；以供 AP 计算的低置信度 0.001 推理时，这些图合计产生 281 个检测框，此值不能作为运营告警误报率。逐图评估耗时约 **13 毫秒**，包含读取、预处理、模型及合并操作，并非纯模型延迟。原始结果在服务器 `reports/pv_hsd_yolo26s_full_val_coco_v1.json`，本地复制于 `reports/pv_hsd_validation_comparison/full_baseline.json`。

2026-10-20 实例重启后检查服务器文件，确认切图训练已在原 4090 上完成，第 **69 轮**结束（早停耐心 20），用时 **5,460 秒，约 91 分钟**。这次开机分配到 **A10G 24 GB**，所以在 A10G 上用相同脚本重新评估原始整图基线及切图模型，得到以下可直接对比的验证结果；没有读取旧测试图片。

| 指标（571 张验证整图） | 原尺度基线 | 切图训练＋四块推理 |
| --- | ---: | ---: |
| mAP50–95 | 0.3075 | **0.3237** |
| mAP50 | 0.7300 | **0.7494** |
| AR@100 | 0.4305 | **0.4521** |
| 47 张空标签图上的检测数，conf≥0.25 | 28 | 36 |
| 47 张空标签图中有检测的图数，conf≥0.25 | 14 | 20 |
| 空标签图上的检测数，conf≥0.50 | 9 | 11 |
| 空标签图中有检测的图数，conf≥0.50 | 5 | 7 |
| 每原始图端到端耗时（A10G） | 18.5 ms | 31.5 ms |

切图使 mAP50–95 提高 **0.0162**，但固定阈值下的空标签图告警和推理成本同时增加。空标签图未被独立认证健康，这些计数仍只是数据内代理指标；不能据此宣布工业误报率达标。候选只训练一个种子，尚需稳定性与独立航次验证。原始报告复制到本地 `reports/pv_hsd_validation_comparison/full_a10_v1.json`、`tiles_a10_v1.json`；切图 `best.pt` 和协议也已取回本机备份。旧留出测试已被 YOLO 基线读取过，之后的比较不能称为全新盲测。

同日启动 **D-FINE-S** 的 COCO 预训练微调对照，配置仍为 640 输入、batch 8、80 轮、种子 2026，只用同一训练/验证组。代码从前述固定提交导出到服务器独立目录 `research/D-FINE-956d170-pvhsd/`，源归档、预训练权重、配置和数据清单哈希记录在 `reports/pv_hsd_dfine_s_a10_v1_run.json`。日志为 `reports/pv_hsd_dfine_s_a10_v1.log`，结果目录为 `reports/pv_hsd_dfine_s_a10_v1/`。在取得完整验证结果前，不判断该 Transformer 检测器是否优于 YOLO。

### D-FINE-S 完整训练与权重复核

实例关机前完成 48 轮；重启后核对上述全部输入哈希，使用保存的 `last.pth`（含优化器、EMA 和调度器）继续到 **80/80 轮**。恢复后的运行用时 **1 小时 19 分 46 秒**，属于剩余阶段耗时。训练日志最高 mAP 出现在第 77 轮；随后单独加载 `best_stg2.pth`，重新运行同一验证 COCO 评估，复现如下结果：

| 模型 | 验证 mAP50–95 | mAP50 | AR@100 |
| --- | ---: | ---: | ---: |
| YOLO26s 原尺度，前述统一入口 | 0.3075 | 0.7300 | 0.4305 |
| YOLO26s 切图，前述统一入口 | **0.3237** | **0.7494** | **0.4521** |
| D-FINE-S，官方验证入口 | 0.3034 | 0.7309 | 0.4510 |

三者使用相同 571 张验证原图和 COCO 指标，但 D-FINE 使用官方无 NMS 后处理，YOLO 使用前述 NMS；这反映当前各自完整推理方案的结果，不是把后处理完全消除后的骨干比较。D-FINE 在本设置下未超过切图 YOLO，故不因架构是 Transformer 就替换现有检测候选。它的独立正常图误报与跨航次表现仍未验收，也尚未做两模型的互补集成评估。

最终训练日志在服务器 `reports/pv_hsd_dfine_s_a10_v1/log.txt`，复核结果在 `reports/pv_hsd_dfine_s_a10_v1_best_validation/summary.json`。阶段二存在重新载入阶段一最佳状态的行为，因此保存权重中的 `last_epoch` 不直接代表外层循环轮号；本结论以**已保存权重重新验证**为依据。日志及最佳权重均另行取回本机备份，原测试分区未读取。


### 逐图预测缓存与错误归因

此前仅保留 COCO 汇总，无法从本机报告分析具体漏检或评估集成互补。`evaluate_pv_hsd_tiled.py --predictions-output` 新增可选整图坐标缓存：记录标注文件/检查点/实际读取验证原图的 SHA-256、全部图像 ID（包括无预测图）、置信度与 NMS 参数。保留现有内部裁块边缘过滤及整图 NMS；不改变训练、不读取测试。缓存先原子保存，再做 COCO 汇总，已有目标文件不覆盖。

本机具备同一切图权重、571 张验证图、torch 2.8.0、Ultralytics 8.4.182 和 MPS GPU；云网关关闭连接期间启动本机 **推理评估**，不是 CPU 预训练或新训练实验。Mac MPS 耗时不能与 A10G 时延混为同一硬件结果，数值差异也需重核。目录 `reports/pv_hsd_tiles_mps_cache_v1*`，启动时尚无最终结果。

`audit_pv_hsd_predictions.py` 在固定 confidence .25/.50、IoU .50/.75 下按分数从高到低进行一对一匹配，输出每幅原图 TP/FP/FN、漏检标注 ID、空标签图告警，并将剩余检测区分为已匹配目标的重复框、IoU≥.1 的定位错误和背景候选。它验证缓存标注/图像 ID，不重新拟合模型或优化阈值。该贪心诊断不是 COCO AP/AR；空标签图也未经独立健康认证，不能将这些计数当现场误报率。当前先定位失效，再决定集成或训练改进，未宣称集成有效。

```bash
.venv-detect/bin/python src/evaluate_pv_hsd_tiled.py \
  --checkpoint reports/pv_hsd_yolo26s_tiles_cuda_v1/weights/best.pt \
  --output reports/pv_hsd_tiles_mps_cache_v1_summary.json \
  --predictions-output reports/pv_hsd_tiles_mps_cache_v1.json --device mps --mode tiled
.venv-detect/bin/python src/audit_pv_hsd_predictions.py \
  --annotations data/processed/pv_hsd_grouped_v2/coco/instances_val.json \
  --predictions reports/pv_hsd_tiles_mps_cache_v1.json \
  --output reports/pv_hsd_tiles_mps_error_audit_v1.json
```


本次 MPS 直接后处理出现 Ultralytics NMS 超时警告，存在跳过后续裁块的风险；已停止这次评估，未保存或采用该结果。随后只把模型原始输出移至 CPU 执行同一库的 NMS，模型前向继续使用 MPS，重新从第一幅图开始。缓存和汇总明确记录推理设备与 NMS 设备；新流程已越过 250/571 张图，尚待完整 COCO 与错误审计。CUDA 默认后处理不变。这次修复旨在避免评估丢失预测，不是新模型或性能提升声明。


本机缓存及完整审计现已完成：571/571 张图均记录哈希，COCO mAP50–95 **0.323625**、mAP50 **0.749426**，与 A10G 切图模型的 0.323727 接近；不是新训练增益。MPS 前向＋CPU NMS 端到端约 **101 ms/原图**，不能替换 A10G 的约 31.5 ms 时延。

| 固定诊断条件 | TP | FP | FN | Precision | Recall | 47 张空标签图中告警图数 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| conf .25，IoU .50 | 2553 | 1445 | 648 | .6386 | .7976 | 20 |
| conf .50，IoU .50 | 2065 | 497 | 1136 | .8060 | .6451 | 7 |
| conf .25，IoU .75 | 1147 | 2851 | 2054 | .2869 | .3583 | 20 |

conf .25 / IoU .50 的 1,445 个 FP 分为重复框 **8**、定位错误 **560**、背景候选 **877**；这里“背景”仅指与已标框 IoU<.1，也可能含漏标，不能断言全是模型幻觉。IoU .75 的召回仅 .3583，提示框定位是主要研究方向之一；单纯加强 NMS 去重难以解释大部分错误。后续优先复核这些逐图错误及标注一致性，再比较更精细定位训练或互补检测器，不能凭诊断计数断言某种方法已有效。完整结果 `reports/pv_hsd_tiles_mps_cache_v1_summary.json`、`reports/pv_hsd_tiles_mps_error_audit_v1.json`，预测缓存同前；测试图未读取。
