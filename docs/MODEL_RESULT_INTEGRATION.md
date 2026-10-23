# 三模型结果接入与设备监控日志

## 交付边界

本次补齐结果接收、设备关联、数据库日志、人工核查与既有通知 outbox 的连接，不部署或运行模型。
本次仅交付后端接口，不修改前端。设备监测页面仍使用原有演示快照；数据库监控日志需要前端同学按本协议对接。
Agent 当前的事件工具仍读取原来的快照，不能声称它已经自动读取本模块日志。
不写回设备固件或现场 SCADA 的日志，不自动控制设备。

## 数据流程

模型调用方/调度服务上传数据到模型服务 → 得到 competition-inference-v1 JSON → POST /api/model-ingestions。
后端逐条关联设备，保存原始结果及版本，追加 equipment_monitor_log，合格的疑似异常生成通知 outbox。
日志保存在业务数据库，支持分页和筛选。前端可每 5 秒读取新记录、按秒显示更新时间；具体页面由前端同学实现。
通知由已有后台 worker 异步发送，不阻塞接收请求。没有设备负责人时保留通知问题记录，不向无关人员发送。

模型服务当前没有主动回调业务系统。部署模型时仍需让调度服务在识别成功后调用接收接口，并对未接收条目重试。
这一步不能仅靠“模型接口能返回 JSON”自动发生。

## 结果格式

本项目 exp/gpu-model-validation 分支 competition_infer.py 输出格式兼容本接口；serve_competition.py 额外返回 request_id。
每次 1–100 条；整个请求不超过 64 KiB；超限时分批，使用不同 request_id。
原始模型 task/output 不能替换成大模型生成的文字。

```json
{
  "schema_version": "competition-inference-v1",
  "request_id": "调用接口返回的请求编号",
  "evidence": [{
    "evidence_id": "不可重复用于不同内容的证据编号",
    "plant_id": "plant-a",
    "asset_id": "INV-01",
    "observation_id": "capture-01",
    "observed_at": "2026-10-23T10:00:00+08:00",
    "task": "detection",
    "model_version": "模型权重版本或 SHA256",
    "threshold_version": "判定配置版本",
    "data_kind": "synthetic_demo",
    "quality": "ready",
    "quality_reason": "ready",
    "decision": "anomaly",
    "score": 0.93,
    "model_output": {
      "detections": [{"class":"hotspot", "score":0.93, "bbox_xyxy":[10,20,80,100]}]
    }
  }]
}
```

真实采集用 site_observation，研究数据用 public_research，演示用 synthetic_demo，不允许省略来源。
task 为 timeseries/classification/detection；decision 为 normal/anomaly/uncertain/blocked。
quality 为 ready/uncertain/blocked/missing。score 可省略，提供时必须是 0–1 数字；分数不是统一校准的故障概率。
分类模型的 model_output 可以包含 predicted_class、class_probabilities，时序模型可以包含 threshold、features、context_minutes。
后端保留这些字段，不自行重新猜测模型阈值。模型输出 decision 才是模型服务按对应阈值计算的判定。

asset_id 和 observed_at 必须来自真实设备关联和实际采集时间；图片不能推断设备编号。时间需要时区，不接受不存在或超过当前 5 分钟的日期。
系统设备编号与 asset_id 一致，且 plant_id 一致时直接关联；不同则管理员保存映射。
研究或演示数据不会覆盖真实采集模型状态，两者的告警合并范围也独立。

## 接收回执、去重与失败处理

- 第一次有效接收返回 201；同 request_id 同完整内容重试返回 200，replayed=true，不重复写入。
- 同 request_id 不同内容返回 409，要求新请求编号。
- 跨请求同 evidence_id 同内容返回 duplicate；同编号不同内容逐条拒绝，不覆盖历史结果。
- 同请求里格式有误、设备未关联等单条业务错误，只拒绝该条。其余条目继续保存，status=partial。
- 每条失败提供 index（从 0 开始）、code、reason（原因）、action（修改办法）。前端对接时应显示为“第 N 条”。
- 修正失败条目后用新 request_id 提交；已成功 evidence_id 无需改变，后端自动识别重复。
- 数据库等基础设施故障整笔回滚，不留下成功回执，可保持原 request_id 重试。
- 一条质量不合格的结果也会保存，日志说明原因；业务判定降为 uncertain，原 decision 保留在原始 JSON 中，不生成异常通知。
- 同设备、模型、数据来源、疑似类型、30 分钟采集时间桶内合并疑似异常通知；每次结果仍分别留日志。
  这只是避免重复通知，不代表三个模型已经融合诊断，也不代表故障已确认。

日志采集时间用于排序；写入时间单独保存。晚到的旧结果不覆盖同模型同来源的新状态。
原始观察值不被修改，监控与人工核查以追加日志记录。没有公开修改/删除日志接口。
历史结果与人工核查只补索引日志，不重新生成历史通知。

## 接口与权限

| 接口 | 用途 | 权限 |
| --- | --- | --- |
| POST /api/model-ingestions | 接收完整模型 JSON | 操作员/管理员会话，或模型专用 Bearer |
| GET /api/model-ingestions/:request_id | 查询逐条回执 | 同上 |
| GET /api/monitoring/equipment | 数据库设备及各模型/来源最新结果 | 登录用户 |
| GET /api/monitoring/logs | 日志列表 | 登录用户 |
| GET /api/monitoring/logs/:id | 原始结果、采集引用、版本、人工历史、通知关联 | 登录用户 |
| GET /api/model-asset-mappings | 查询设备编号映射 | 登录用户 |
| POST /api/model-asset-mappings | 保存映射并审计 | 管理员 |
| POST /api/human-confirmations | 追加人工确认/排除/待调查 | 管理员 |

日志筛选 equipment_id、task、data_kind、decision，limit 默认 25，最大 100，offset 非负。
映射请求为 {"plant_id":"plant-a","asset_id":"camera-module-01","equipment_id":"INV-01"}。
映射不能跨电站。系统设备和负责人到通知中心配置。

模型专用凭证只从服务器 FUMAI_MODEL_INGEST_TOKEN 读取（至少 32 字符），没有默认凭证；不写入网页或 Git。
接入凭证不能读取人员、设备日志、通知或修改设备映射。不配置时仍可通过现有操作员会话联调。
公网 HTTPS 配置之前，应由同机服务走 127.0.0.1:8787，外部模型服务走 SSH 私有转发；不要通过公网 HTTP 发送服务凭证。

## 给前端同学的对接事项

本次不交付页面。现有页面无需调整即可继续运行；新增日志与映射功能需单独接入。

1. 使用现有登录 Cookie 调用 GET /api/monitoring/equipment，展示数据库设备及各模型、各来源的最新结果。
2. 使用 GET /api/monitoring/logs 按 equipment_id/task/data_kind/decision 筛选，limit 默认 25，用 offset 翻页。
3. 点击日志时调用 GET /api/monitoring/logs/:id，展示原始 JSON、分类/检测框、版本、采集引用、人工历史与关联通知。
4. 管理员调用 POST /api/model-asset-mappings 设置同电站的设备编号对应关系；普通用户只读。
5. 如需联调入口，操作员调用 POST /api/model-ingestions 提交模型完整 JSON。不要在前端配置机器接入凭证。
6. 接收回执中 accepted、duplicates、rejected 分别表示新增、重复与待修改条目。replayed=true 表示整次请求已接收过，不能当成新增成功。
7. rejected 条目提供 reason 与 action，按 index+1 显示条目序号。部分失败时继续展示已接收条目。
8. 低质量结果的 items[].warnings 和日志 summary 应显示给用户，不应把“已接收”当成“识别成功”。
9. POST /api/human-confirmations 可追加人工核查，字段为 model_result_id、status、note；actor 由后端取当前管理员。
10. 网络超时后以相同 request_id、相同内容重试；修正错误条目后使用新请求编号。不能为自动重试反复生成新 evidence_id。

前端不得将演示数据标成真实运行状态，不得使用模型的 normal 状态覆盖另一个模型的疑似异常。
线上 SMTP 已启用；联调应使用无负责人的专用演示设备，避免给员工发送测试邮件。

## 验证

notifications 下执行 node --test，包括真实 HTTP 服务、角色权限和只限接收接口的机器凭证测试。
测试使用临时数据库、mock 通知渠道，不调用 GPU、不发送真实邮件。

## 给模型开发同学的现成回传工具

`notifications/integrations/deliver_model_result.py` 仅依赖 Python 标准库，接收已有结果文件，不加载 GPU 模型。
模型推理成功后，调度任务可以运行：

```sh
python3 notifications/integrations/deliver_model_result.py \
  --input reports/job/result.json \
  --receipt /private/model-receipts/job.json
```

接入凭证从进程环境变量读取，不能直接写入命令行或 Git。默认调用本机业务接口。
支持 HTTPS 或 loopback/SSH 转发地址，拒绝公网明文与重定向，避免凭证转发到其他地址。
服务短暂不可用会最多重试 3 次，保持请求编号；CLI 报告缺少 request_id 时按原内容摘要生成稳定编号。
逐条回执保存为 0600 文件，保留所有成功项和失败原因；部分拒绝退出码为 2，网络/配置失败为 1，全部接收为 0。
工具不会修改原报告、不会反复执行推理。自动调度与现场数据采集仍由模型服务部署时挂接。

## 补充资料核对

用户提供的《光伏电站运维》为平台设计与实现参考资料。已核对第 3.2 节模块划分、第 3.4.2 节设备台账/告警/遥测/遥信表，以及第 4.5 节组件诊断。
本次实现与其中“以设备编号关联数据、异常生成告警、保留确认与追溯记录”的业务关系一致。
模型疑似异常作为独立业务事件存储，不覆盖现场遥测值，不伪造现场 IEC104 遥信点值。

| 参考资料中的能力 | 本次实际范围 |
| --- | --- |
| 设备台账关联与追溯 | 使用现有 equipment 主键，增加同电站模型编号映射 |
| 异常诊断与告警 | 保存原始识别结果、判定、质量、版本和采集时间，生成通知 |
| 告警确认与处理追踪 | 支持人工确认/排除/待调查及说明留痕，完整维修工单状态流尚未实现 |
| IEC104/Modbus 及点号采集 | 本次不实现采集器，输入由模型调用方提供 |
| 完整设备档案与分层拓扑 | 由设备业务模块完善，本次不重建其表结构 |
| 三维场景、视频、日月报、发电预测 | 不属于本次接收链路，不声明已完成 |

参考资料的 Spring Boot/MySQL/Unity 等方案不自动改变目前独立业务服务的部署架构。
后续由主业务后端统一设备主键后，可以保留本协议，将接收与日志能力迁入对应模块。
