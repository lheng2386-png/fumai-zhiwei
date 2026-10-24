# 光伏设备运维后端设计与接入状态

## 队长要求对应的后端交付

目标页面不是一张静态大屏。后端需要同时提供资产层级、实时点值、告警故障、模型诊断和发电报表，页面才能形成“场景/一次图 + 设备参数 + 告警 + 统计”的联动。

| 页面目标 | 后端数据与接口 |
| --- | --- |
| 状态监视、设备树 | `substation_info` 至 `panel_series_info`、`equipment_info`；`GET /api/operations/device-tree` |
| 实时监测、一次接线图 | `iec104_point`、遥测/遥信、`latest_point_value`；snapshot、topology、SSE stream 接口 |
| 异常分析 | `warning_info`、`fault_history_info`；告警查询和确认接口 |
| 报表管理、发电日历 | `power_generation_report_info`；generation、calendar 接口 |
| 组件诊断 | `model_inference_run/result`；统一模型结果写入和 diagnosis 查询接口 |
| 图档与巡检 | `equipment_materials_info`、`equipment_inspection_info` |
| Agent 工具 | `/agent-api/bootstrap` 直接读取业务库中的告警和模型结果，并返回数据来源状态 |

## IEC 60870-5-104 数据边界

采集进程负责与现场 RTU/网关建立 104 连接、总召唤、时钟同步、断线重连和顺序号管理。本服务不在 HTTP 进程内维持 104 长连接，而是接收采集进程标准化后的 ASDU：

```json
{
  "endpoint_id": "gateway-id",
  "observed_at": "2026-10-24T08:00:00+08:00",
  "asdu": { "type_id": 13, "common_address": 1, "cause": 3, "sequence": 1024 },
  "points": [
    { "ioa": 1001, "value": 412.5, "quality": "good" }
  ]
}
```

点表用 endpoint + IOA 唯一映射设备和含义。后端在同一事务中写入历史表和最新值缓存；设备、点、时间戳的唯一约束阻止重复上送覆盖。质量位和原始 ASDU 随记录保存。若点表配置了告警值，遥信上送可幂等生成业务告警。

## 三套模型接入契约

已有模型脚本必须通过 `POST /api/operations/model-results` 写入统一结果，至少提供：`request_id`、`model_family`、模型/阈值版本、数据时间范围、数据质量、设备、score、decision 和原始结果。`request_id` 全局唯一；`uncertain`、`blocked` 与 `anomaly` 分开存储，不能被界面写成确认故障。

支持的模型族为：`power_reference`、`dc_five_class`、`opencem`、`deep_tcn`。仓库中的多条研究链路是独立实验，只有真正调用写入接口后才会显示为已接入。

## 性能与存储

- SQLite 开启 WAL，并用 `latest_point_value` 保存页面所需的最新点值，避免每次扫描时序历史。
- 遥测、遥信和气象表按设备与时间建索引；`data_retention_policy` 定义热数据保留天数，管理员可触发滚动清理。
- 采集使用批量写入，实时页面使用 SSE，每两秒只在快照变化时推送。
- 当前部署只有一个 SQLite 文件，不具备真正的主从读写分离。需要读副本时迁移到 PostgreSQL/MySQL，再把查询连接指向只读副本。

## 当前真实状态

数据库、接口、幂等写入、最新值缓存、告警展开和 Agent 业务库入口已经实现。由于团队尚未提供现场 104 主机、公共地址、IOA 点表，以及三套模型的生产调用命令/模型文件，接口会明确返回 `unconfigured`、`configured_offline` 或 `empty`，不会自动填充演示数据，也不会宣称现场链路已打通。

接通现场前需要团队提供：场站/设备台账、104 网关 IP:2404、公共地址、IOA 点表、遥信告警语义、服务器到现场网关的网络通路，以及每套模型的输入文件位置和运行命令。

