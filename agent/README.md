# 运维证据助手

Python 3.12，无第三方服务端依赖。当前默认运行构造样例和规则分析；可配置 Responses 模型与实测事件快照。

运行、数据合同、模型接入、认证、部署和现场试运行条件见 [完整说明](../docs/INDUSTRIAL_AGENT.md)。

```bash
python -m unittest discover -s agent/tests -v
```

测试不调用外部模型、不需要密钥；HTTP 测试使用本机回环地址，需要本机网络权限。
