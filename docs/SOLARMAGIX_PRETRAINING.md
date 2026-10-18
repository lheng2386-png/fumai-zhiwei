# SolarMagix 多场站时序预训练

目标是先用真实多场站运行序列训练一个可复用的时序编码器，再研究如何迁移到带确认故障标签的数据。这里的指标是**遮挡输入重建误差**，不是故障识别率。

数据来自 [Mendeley Data v2](https://doi.org/10.17632/4zgsckxpdy.2)（CC BY 4.0）。当前已下载并验证 SHA-256 的小时级逆变器、电站、设备和状态映射文件。文件不随 Git 仓库分发；可在本机或服务器复现下载，本地清单和数据审计在 `data/raw/solarmagix/`。

## 数据处理

小时级逆变器表含 112,249 行、50 台逆变器、7 个电站。记录主要覆盖白天，最长连续片段只有 21 小时，因此不能直接用 24 小时连续窗口。预处理使用**同一设备的连续 8 小时窗口**，步长 4 小时；隔夜和缺测处断开。剔除状态为 ERROR、非有限值、负功率和效率超过 110% 的行。保留交流/直流功率、设备/环境温度、短波与倾斜面辐照 6 个输入。异常的 `shortwave_radiation_instant` 和由功率计算的效率不作为特征。

训练区 5 个电站、31 台逆变器；验证区 1 个电站、8 台逆变器；留出测试区 1 个电站、11 台逆变器。截断上下分位数、中位数和 IQR **只在训练区拟合**。当前生成 12,169 / 1,932 / 2,743 个训练/验证/测试窗口。留出测试电站 `b596…` 尚未用于模型选择。前期在另一电站 `e2afd…` 做过 3 轮本机流程检查，其误差不能当作新独立测试，也不应据此选模型。

```bash
python3 src/fetch_solarmagix.py
python3 src/audit_solarmagix.py
python3 src/prepare_solarmagix.py
python3 -m unittest tests.test_solarmagix -v
```

## 模型与运行

`pretrain_solarmagix.py` 使用项目自研的多尺度时序 CNN：并行 3/7/15 卷积核提取不同时间尺度，随机遮挡连续两小时及部分传感器通道，只在遮挡位置计算重建损失。验证电站决定最佳轮次；之后才读取留出电站一次。默认宽度 64、深度 3、最多 30 轮、早停耐心 6 轮。

在带 PyTorch CUDA 的 A10 环境中，从仓库根目录运行：

```bash
python3 -c 'import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))'
python3 src/fetch_solarmagix.py
python3 src/prepare_solarmagix.py
python3 src/pretrain_solarmagix.py --data data/processed/solarmagix_8h.npz --output reports/solarmagix_ssl_a10_v1 --device cuda --batch-size 256
```

如果显存不足，改用 `--batch-size 128`；若仍不足，依次减小 `--width`，并记录不同配置。输出的 `best.pt` 含编码器、特征顺序和训练区归一化参数，`results.json` 含验证与留出电站误差、零填充基线、训练轮次及数据文件哈希。只克隆 Git 仓库不足以复现，须先执行下载命令或上传已校验的原始 CSV。

本机 Apple GPU 上，宽度 32/深度 2 的 3 轮**流程检查**用约 6 秒训练；不能据此推断 A10 的实际时长或完整实验效果。实际 A10 用时以服务器日志为准。迁移到 La Réunion 故障模型还需要特征适配器和严格的验证协议，不能直接把重建误差当作故障性能提升。
