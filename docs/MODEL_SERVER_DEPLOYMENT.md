# 比赛模型服务部署记录

日期：2026-10-21。网页目标服务器为用户指定 Ubuntu 主机；现有前端与 Agent 保留，增加同源模型接口。

## 拓扑

浏览器 → 网站 Nginx `/model-api/` → 网站本机 `127.0.0.1:18770` → GPU 主机到网站的反向 SSH 隧道 → GPU `127.0.0.1:8770` FastAPI → 固定三模型权重。

网站只有 2 vCPU/2GB，不安装 PyTorch 或训练模型。GPU 使用 A10G，推理 device=cuda。模型入口不替换现有 `/agent-api/` 或尚待开发的 `/api/`。

- 健康检查：`http://47.122.123.147/model-api/health`
- 推理：`POST /model-api/v1/infer/{timeseries|classification|detection}`
- 证据策略：`POST /model-api/v1/review`
- 结果查询：`GET /model-api/v1/jobs/{request_id}`
- 人工反馈：`POST /model-api/v1/feedback`
- 模型契约：`COMPETITION_MODEL_INTERFACE.md`；任务拆分：`FRONTEND_BACKEND_TASKS.md`。

该路由是比赛演示接口，尚未接入业务登录和跨用户任务权限；不要往公开演示入口上传私人联系方式或生产数据。业务认证和通知系统另由后端交付，当前没有发送短信/邮件。

## 文件和运行状态

GPU 根目录 `/root/fumai-competition-api`：`releases/<commit>/src` 保存代码，`models-v1/` 保存三份权重和归一化文件，`state/` 保存输入/结果/feedback.sqlite，`api.log` 与 `tunnel.log` 为日志，`api.pid`/`tunnel.pid` 为进程号。代码交付提交 `92347cef5463336886a04a5f502ccac05fac1a33`。

权重通过配置 SHA-256 校验；没有临时训练或下载来替代部署。隧道使用专用密钥，网站授权限制到本机监听 18770；私钥未进入仓库。

网站 Nginx snippet `/etc/nginx/snippets/fumai-model.conf` 来自 `ops/model-location.conf`。原站点配置在启用前创建 `*.before-model-api-20261021` 备份，`nginx -t` 通过后 reload，现有 frontend/agent release 未切换。

GPU API 与隧道当前以脱离 SSH 会话的进程运行；隧道断开会重试，但**不保证云实例关机/重建后自动恢复**。比赛前先检查公网 health，实例重启需恢复 GPU 服务及隧道，不要把网站在线等同 GPU 在线。没有增加云平台自动关机任务。

## 验证与回退

从仓库可执行真实 HTTP 验证：

```bash
python scripts/smoke_competition_api.py --url http://47.122.123.147/model-api \
 --inputs reports/competition_real_inputs_v1 --output reports/aliyun_smoke_new
```

样例仅来自训练/开发源，用于验证接口可运行，不作为新泛化分数。输入文件不提交 Git，准备步骤见模型契约。本轮公网实测：三个 infer 均返回 200；跨公开来源的复核返回 single_source_review 并排除不同设备；反馈返回 201、历史查询返回 200、原结果 SHA 保持不变；原首页和 Agent health 均返回 200。`/api/health` 仍返回 503，明确业务后端尚未接通。记录在本地 reports/competition_aliyun_http_v1，未把样例输入上传 Git。

回退本次网站入口：从站点配置移除新增 `include /etc/nginx/snippets/fumai-model.conf;`，`nginx -t` 后 reload；不动 `/agent-api/`、frontend/current、agent/current。停止 GPU 本服务前检查 PID 文件对应命令确为 competition API/tunnel，禁止批量 kill Python 或停训练。保留 state 数据供排查。
