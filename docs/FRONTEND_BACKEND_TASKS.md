# 两天比赛版：前后端开发任务清单

负责人自行认领前端 A、后端 B；项目负责人 C 负责模型、智能体、云边端。三人都写代码。目标是在现有系统中完成“上传 → 真实推理 → 展示证据 → 人工复核”的完整演示。

## 已查看的仓库和线上代码（2026-10-21）

仓库：`lheng2386-png/fumai-zhiwei`。不能只看 main：当前 main 为 `2049e75`，模型开发在 `exp/gpu-model-validation`，网页和智能体在成员分支。

| 部分 | 线上版本/入口 | 当前事实 |
|---|---|---|
| 前端 | `feat/event-alert-bubbles-stian45663`，`df72210` | `/srv/fumai-zhiwei/frontend/current/dist`；原生 JS/CSS；已有概览、事件、设备、复核、模型和智能体界面 |
| 智能体 | `fix/agent-intent-routing-stian45663`，`5773499` | Python 标准库 HTTP 服务，systemd `fumai-agent`，127.0.0.1:8091；已有分析任务、进度流、历史和确认草稿 |
| 业务后端 | `/api/` | Nginx 当前明确返回 503；`feat/backend-yzx` 与 main 同提交，尚不能视为已完成业务接口 |
| 模型接口 | `exp/gpu-model-validation` | FastAPI 三模型真实权重推理、证据复核、结果查询和追加式人工反馈；接口契约见 COMPETITION_MODEL_INTERFACE.md |

现有前端 `dist/data.js` 是构造示例，事件复核部分写 localStorage；智能体有独立服务端存储。必须区分两者，不把示例曲线、智能体演示快照当作新模型实测结果。已有 `/agent-api/` 继续保留。

## 部署分工和固定边界

网页/业务服务器：用户指定 Ubuntu 公网主机，2 vCPU、2GB 内存；负责 Nginx、网页、业务存储及模型请求转发。GPU 主机负责 CUDA 推理。网页以同源 `/model-api/` 调用模型服务，去掉此前缀后对应模型原生路由；GPU 不可用时显示不可用，不能返回模拟预测。

本次增加的入口不等于 `/api/` 业务后端已经完成。前端不需要 SSH、密码、模型路径。上传不能触发训练。模型反馈记录在 GPU 的独立持久化目录；B 必须完成业务侧索引/结果留存，避免 GPU 实例停止后业务历史不可查。

## A：前端同学的代码任务

| 优先级/编号 | 要写的功能 | 可验收结果 |
|---|---|---|
| P0 F1 | 在现有模型页新增三个任务入口，不重写全站 | 可切换时序 CSV、组件分类、整图热斑检测；显示服务健康状态 |
| P0 F2 | 上传表单 | file、plant_id、asset_id、observation_id、data_kind；图像必须带有时区的 observed_at；明确公开样例/模拟/实测来源 |
| P0 F3 | 接真实推理 | POST `/model-api/v1/infer/{task}` multipart；显示处理中并禁重复提交；成功保存 request_id；失败保留用户输入 |
| P0 F4 | 时序结果 | 根据 evidence 的 observed_at、score、model_output.threshold 显示窗口结果；uncertain/blocked 单独显示，不能当正常；不是功率预测曲线 |
| P0 F5 | 分类结果 | 显示 predicted_class 和 class_probabilities，保留原英文类名和中文说明；低分拒判说明；不要把 softmax 当真实故障概率 |
| P0 F6 | 检测画框 | 在原 640×512 图上画 bbox_xyxy；按图片实际显示尺寸等比映射，缩放后仍对齐；空框显示“未检出候选，待核实” |
| P0 F7 | 复核表单 | POST `/model-api/v1/feedback`；四种状态、reviewer、必填 note；GET jobs 后刷新仍能看到历史；模型原结论与人工结论分别展示 |
| P0 F8 | 错误和来源 | 413 文件过大、422 格式/时间错误、503/502 GPU 不可用均有明确提示；展示模型版本、数据来源和质量原因 |
| P1 F9 | 协同证据卡 | POST `/model-api/v1/review`；显示 included/excluded 依据、status、priority；不同设备/时段不得前端硬凑联合故障 |
| P1 F10 | 业务接口替换 | 与 B 对齐后把事件、复核历史从 localStorage/demo 数据切到 `/api/`；示例模式保留醒目标识 |

调用示例：`new FormData()` 添加上述字段，用 `fetch('/model-api/v1/infer/detection', {method:'POST', body:form})`。不要手设 multipart Content-Type，让浏览器添加 boundary。原生模型接口是同步请求；不要轮询不存在的任务进度接口。`GET /v1/jobs/{request_id}` 查询已经完成的结果。业务异步队列由 B 单独实现并注明契约。

## B：后端同学的代码任务

| 优先级/编号 | 要写的功能 | 可验收结果 |
|---|---|---|
| P0 B1 | 建立业务服务和健康检查 | 认领自己的分支；确认 Python 入口/环境和未占用端口；`GET /api/health` 返回真实 ready，而非固定成功 |
| P0 B2 | 上传与任务索引 | 保存业务 job_id、模型 request_id、任务、设备、采集时间、来源、输入 SHA、状态和错误；上传不接受任意服务器文件路径 |
| P0 B3 | 模型适配器 | 固定 base URL，超时、错误码保真；从模型回包保存完整 JSON、bundle/权重/阈值版本；测试 GPU 不可用，不能假装成功 |
| P0 B4 | 历史与详情接口 | 建议 `GET /api/model-jobs`、`GET /api/model-jobs/{id}`，分页和设备/任务过滤；服务重启后历史不丢；先写 OpenAPI/示例与 A 对齐 |
| P0 B5 | 复核持久化 | 与模型 feedback 关联并留存追加历史；人工状态不覆写原始预测；明确复核人是页面输入还是已认证用户 |
| P0 B6 | 示例和实测隔离 | 演示设备与实际设备不能自动映射；检查 plant_id/asset_id/observation_id、时区；不同公开数据集不做伪联合验证 |
| P0 B7 | 发布与回退 | 新服务独立 systemd、独立数据目录；测试通过再替换 `/api/` 的 503；nginx -t 后 reload；保留上一 SHA，不覆盖 frontend/agent current |
| P1 B8 | 接现有智能体证据 | 查看 agent/core.py 的 Snapshot 输入契约与 import_snapshot.py；把真实结果显式转换、注明版本和未知项，不把任意模型 JSON 直接塞给问答 |
| P1 B9 | 业务侧异步任务 | 若同步演示耗时影响操作，再增加 queued/running/succeeded/failed、轮询、幂等键和受限并发；模型服务当前串行，不能无限并发 |

业务列表/认证/业务队列属于 B 的待开发接口，区别于已经存在的模型接口；不能在页面中默认它们已可调用。权重与原始数据不提交 Git，密码/密钥不写仓库。

## C：负责人继续负责

固定和改进三个模型、匹配阈值及归一化文件；提供真实样例、模型 JSON、质量规则和来源说明；维护 GPU 服务、证据策略和模型版本。智能体算法、云边端研究归 C。A/B 可以编写智能体 UI 和业务适配代码，不能擅自改模型阈值、声称 SOTA 或工业已验收。

## 两天顺序和联调验收

第一天前半：A 完成 F1–F3，B 完成 B1–B3；冻结接口字段。第一天后半：A 完成 F4–F8，B 完成 B4–B6，至少一条真实图像请求贯通网页。第二天前半：三任务全部贯通，完成 F9/B7，失败场景和重启测试。第二天后半：录屏、样例备份、README 操作步骤和比赛演示；有余力做 P1。

共同验收：

- 三种真实权重分别成功推理；显示版本与 request_id，可再次查询。
- 一条常量/低质量输入显示拒判；损坏图像 422；超大文件 413。
- 三个不同公开来源不能变成“同设备多模态确认故障”。
- 人工复核提交并刷新可见；原始推理结果未修改。
- GPU 断联显示服务不可用；已有首页与 `/agent-api/health` 仍正常。
- 每个人提交代码、检查结果、交付 SHA 和可复现操作；依照 CONTRIBUTING.md 开 PR。不要在服务器直接修改队友代码后遗漏回仓库。

两天交付定位为可演示的比赛原型；生产环境泛化、认证完善及现场验收另排阶段，不把尚未完成的指标写成已有成果。
