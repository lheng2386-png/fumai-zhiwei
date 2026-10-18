# 伏脉智维前端原型 v0.1

关联任务：lheng2386-png/fumai-zhiwei#1。负责人：STIAN45663。开发分支：`feat/frontend-stian45663`。

静态 HTML/CSS/JavaScript 工作台，无第三方前端运行依赖。五个视图：电站概览、异常事件、设备监测、复核记录、数据与模型。支持关键词/设备/证据/状态筛选、日周月示意曲线、事件证据抽屉、模拟复核与 CSV 导出；适配桌面和移动屏幕。

## 本地运行

需要 Node.js 18 或以上。在仓库根目录执行：

```bash
cd frontend
npm run check
npm start
```

打开终端输出的 `http://127.0.0.1:4173`。也可用 Python：`python -m http.server 4173 --bind 127.0.0.1 --directory dist`。

## 数据与复核边界

- `dist/data.js` 全部为手工构造的样例，`source=constructed_demo`，不是原始 PVDAQ 数据或模型实测输出。周/月图表也是构造示意；设备快照与事件历史时段分别展示。
- 24 台设备与模块组织参考项目 README。12 条事件用于交互演示，不能当作真实故障或实验评估集。
- 事件本地时间沿用项目语义 `PST8PDT`，字符串不自动转换；新增复核记录保存 UTC ISO 时间，界面明确以北京时间显示操作时间。
- 连续事件按 5 分钟采样，观测跨度为 `(samples - 1) * 5` 分钟；参考电量差不等于可挽回损失。
- 新复核只存浏览器 `localStorage` 的 `fumai-demo-reviews-v1`，没有发送到后端，也不会自动生成训练标签。导出记录包含全部追加历史及预置样例状态。站点数据清理或更换浏览器会丢失本机记录。
- 导出的 CSV 含样例来源标记，并对公式前缀做转义。所有人工说明在页面中均经 HTML 转义。
- 未实现登录、工单派发、实时采集、大模型智能体、云边端控制或真实模型推理。

## 后端接入约定

当前数据入口为 `window.FumaiDemo`；查询和复核逻辑集中在 `dist/app.js` 的 `getEvents`、`filteredEvents` 及复核提交处理。后续将数据适配层替换为后端请求即可保留页面结构。以下为待双方约定的建议接口，不代表服务已经实现：

| 资源 | 建议行为 | 字段或语义 |
| --- | --- | --- |
| GET /api/events | 筛选与分页 | `event_id`, `inverter`, `start_local`, `last_sample_local`, `samples`, `evidence`, `review_status`, `reference_shortfall_kwh` |
| GET /api/events/:id/evidence | 逐点曲线 | `time`, `actual_kw`, `expected_kw`, `threshold_kw`, `peer_count`, `peer_ratio`, `residual_flag`, `peer_flag`；按时间排序，缺测断线 |
| GET /api/devices | 设备与同群元数据 | 设备 ID、暂定同群、单位、最后观测时间及质量标记 |
| POST /api/events/:id/reviews | 追加复核记录 | `status`, `note`；服务端生成时间与审计信息，追加历史并返回保存结果 |
| GET /api/summary | 数据和模型来源 | 数据版本、输入校验值、模型版本、场站时区、样本范围 |

状态值与现有项目一致：`unreviewed`、`needs_investigation`、`expected_operation`、`data_issue`、`confirmed_fault`。证据值为 `device_specific_candidate`、`shared_or_model_candidate`、`insufficient_peers`。功率 kW，电量 kWh。

## 验收

自动语法检查：`npm run check`。浏览器验收应覆盖桌面与移动布局、五个视图、组合筛选与空状态、证据抽屉、复核必填校验、保存/刷新恢复、历史追加、CSV 下载、HTML 转义与控制台错误。已执行结果见 `VALIDATION.md`。

后续真实数据接入时需加入错误/加载状态、分页、缺测与单位校验，并单独验证后端权限和持久化。
