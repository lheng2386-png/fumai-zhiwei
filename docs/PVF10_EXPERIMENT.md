# PVF-10 高分辨率红外分类：修复划分与首轮模型比较

[PVF-10 发布仓库](https://github.com/wangbobby1026/PVF-Dataset)包含 5,579 张组件裁图，原始 TIFF、110×60 和 112×112 PNG 是同一批裁图的三种表示，不是三倍独立样本。本实验只使用 112×112 RGB PNG。ZIP 已通过 CRC，归档 SHA-256 为 `5bbedd95fd99b98198fcea84017c9243af8d4e8aeef307a3bd77200919195189`。这是**组件裁图十类分类**，不能用来计算整图缺陷检测 mAP。

## 为什么不能沿用发布包的训练/测试目录

发布包训练和测试文件夹的数字前缀对应不同语义类别。例如训练文件夹 `09substring open circuit`，测试文件夹却是 `01substring open circuit`；按数字或文件夹顺序映射标签会错标。`src/prepare_pvf10.py` 按去掉两位数字后的类别名称建立统一映射。

文件名中可解析出原始 DJI 照片标识和拍摄日期。在 112×112 版本中，**378 个原始照片组**同时出现在发布的训练和测试目录；112×112 与 110×60 两个版本之间，同名裁图的发布训练/测试归属还有 **994 张不一致**。这些事实使原目录不适合独立泛化评估。无法从归档核实八座电站各自的照片 ID，因此也不能声称已经做了跨电站划分。

我们按**拍摄日期**重新隔离：训练 2,805 张（14 天）、验证 1,085 张（2023-02-23、2023-04-07）、暂留测试 1,689 张（2023-11-14、2023-11-21）。三部分均有十类，日期和原始照片组不重叠；新划分中没有跨部分的解码像素完全重复。日期是比随机裁图更严格的代理分组，但仍不等于航次或电站独立。测试日期根据类别覆盖选定，不能描述成完全未参与任何设计的外部盲测。原图和新划分文件只留本地，不上传 GitHub。发布仓库没有单独明确的数据商业使用许可，当前只用于研究。

## 第一轮验证集结果

相同新划分、相同 112×112 输入，比较自主残差 CNN（随机初始化）与公开 ImageNet 权重的 ResNet18。两者在本机 Apple MPS 完成训练，按日期隔离的验证集宏平均 F1 早停；模型选择只读取训练/验证文件，**留出测试文件未读取**。
ResNet18 使用 torchvision `IMAGENET1K_V1` 权重，下载文件 SHA-256 为 `f37072fd47e89c5e827621c5baffa7500819f7896bbacec160b1a16c560e07ec`。

| 模型 | 种子 | 验证宏平均 F1 | 最佳轮次 |
| --- | ---: | ---: | ---: |
| 自主残差 CNN | 2026 | 0.4217 | 26 |
| ImageNet ResNet18 | 2026 | 0.5371 | 7 |
| ImageNet ResNet18 | 2027 | **0.5724** | 18 |
| ImageNet ResNet18 | 2028 | 0.4967 | 12 |
| ResNet18 三种子等权平均 | — | 0.5308 | 各自最佳 |

按照预先固定的“最强单模型对比三种子等权集成”规则，选中 **ResNet18 种子 2027**；三种子集成没有增益。三种子单模型均优于自主 CNN，但从 0.497 到 0.572 的波动明显。2027 模型对 `healthy panel` 和 `hot cell` 的 F1 分别为 0.863、0.801；`short circuit panel` 只有 **0.188**（验证 12 张），`string short circuit` 为 **0.372**（30 张），`junction box heat` 为 **0.381**（9 张）。这些少数类和跨日期变化仍阻止生产使用。论文在其划分上的总体准确率与我们的日期隔离宏 F1 不是同一指标，不能直接对比或宣称超过论文方法。

结果和权重保存在本地 `reports/pvf10_date_mps_seed2026_v1/`、`reports/pvf10_resnet18_mps_seed202{6,7,8}_v1/` 和 `reports/pvf10_date_selection_v1.json`。约 102 秒的自主 CNN 训练、42/77/57 秒的 ResNet18 训练不包含数据整理和人工审计；这些时间只是本机 MPS 实测，不是服务器训练估时。

```bash
python3 src/prepare_pvf10.py
PYTHONPATH=src python3 src/train_pvf10.py \
  --output reports/pvf10_date_mps_seed2026_v1 --device mps
for seed in 2026 2027 2028; do
  PYTHONPATH=src python3 src/train_pvf10_transfer.py \
    --output reports/pvf10_resnet18_mps_seed${seed}_v1 --device mps --seed "$seed"
done
PYTHONPATH=src python3 src/select_pvf10.py \
  --runs reports/pvf10_resnet18_mps_seed2026_v1 \
         reports/pvf10_resnet18_mps_seed2027_v1 \
         reports/pvf10_resnet18_mps_seed2028_v1 \
  --baseline reports/pvf10_date_mps_seed2026_v1 \
  --output reports/pvf10_date_selection_v1.json
```

下一步应针对少数类和跨日期成像变化做有控制的增强、预训练骨干比较，并用新航次/新电站的确认标签检验。不能把已有 sUAS 单正射影像或当前 PVF-10 日期验证集重复调参后的分数当作工业性能。
