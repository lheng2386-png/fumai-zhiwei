# 光伏知识与连续追问（Issue #9）

本轮修复基于 dfbb920。新增普通光伏知识通道，覆盖用户原例“光伏逆变器出现欠压，一般是什么原因？”，并补充“判断依据是什么”“为什么这么判断”“有哪些数据支持”等追问表达。

## 回答方式

- 一般知识：独立知识提示词，提供版本化通用说明及当前知识话题的历史，Qwen 直接解释问题。没有设备工具、事件观测或工单权限；不要求先选择事件。热斑等没有专门资料卡的通用光伏问题也可由模型回答，但不标为已验证的手册知识。
- 欠压说明区分交流电网、PV 直流输入与内部直流母线。清晨发生只是一项时间线索；不能仅凭低功率推断欠压，不能把其他机型的阈值或告警码直接套用。
- “如果只在清晨出现呢”接续当前知识话题；打开新的设备分析后，依据追问回到新分析。知识问答后也可以显式说“刚才 inv_07 判断依据是什么”回到该会话的设备证据。
- 设备依据：说明实测与模型参考的差异、同群类别、证据来源和不能确定的内容；不会重新调用完整设备诊断。通信/原因排序等没有数据的追问也会先展示相关观测，再解释缺口，不以路由说明拒答。
- 新会话和访客隔离沿用上一版，不跨用户取上下文。历史查询和帮助不清除当前讨论的知识话题。

## 资料与边界

`agent/knowledge.py` 保存短说明及来源，页面可展开“通用知识参考”。运行时不抓取网页，也不宣称有实时联网检索或完整厂家知识库。

- [SMA Sunny Tripower 事件说明](https://manuals.sma.de/STPTL-30/en-US/391460235.html)：区分交流电网、直流输入不足、温度与绝缘类事件。
- [SMA Sunny Boy 直流启动事件](https://manuals.sma.de/SBxx-1AV-41/en-US/11012315019.html)：辐照、遮挡及设计/接线条件是核对方向，不是本站故障结论。

以上页面于 2026-10-23 核对。短说明是通用转述，不复制完整手册；型号专属操作、阈值与告警码必须另行核对。

知识输出校验禁止伪造设备引用、肯定的现场状态和未经手册提供的电压/绝缘阈值。网络、格式或规则校验失败时保留本地资料说明，并标记 `model_status=failed_fallback`。规则不构成完整的自然语言事实验证器；新增资料应增加来源和对应评测。

## 接口与回归

原接口保持不变。知识回答仍是 `response_type=answer`，增加 `knowledge_topic`、`knowledge_question`、`knowledge_question_ids`、`knowledge_sources` 和版本化 `knowledge_notes`；这些字段与事件证据分开，通用资料不进入 `evidence_ids`。模型适配器使用 `reply_mode=knowledge`，两种协议均禁用设备工具。

测试入口：

```bash
python -m unittest discover -s agent/tests -q
node --test frontend/tests/agent-task.test.mjs frontend/tests/intent.test.mjs
```

浏览器：临时数据库启动 demo 服务，再用 `frontend/tests/knowledge.browser.cjs`；环境变量沿用会话回归的 FUMAI_CHECK_URL / FUMAI_PLAYWRIGHT_MODULE / FUMAI_BROWSER_EXECUTABLE / FUMAI_TEST_OUTPUT。
