# 上传文档知识库、RAG 与 MCP

本版本增加真实的资料建库和检索流程，合并线上已有的会话、连续追问及通知中心功能。知识库不依赖环球人才的 RAGFlow，也不共享两个项目的数据。

## 使用

进入运维智能体，点击“知识库 · 上传与检索”。支持 TXT、MD、DOCX、文字版 PDF，每份最多 6 MB，提取正文最多 30 万字。PDF 服务器依赖发行版的 `poppler-utils`；Word 使用标准库解析。扫描 PDF 尚不提供 OCR，失败提示会说明先做文字识别。

资料上传后立即返回后台任务；按段落或 PDF 页码切分，保存原文件、SHA256 版本、可定位原文和检索索引。页面显示建库阶段、秒级耗时、失败原因、重试、预览和删除。相同文件在同一工作空间自动去重。失败资料不会参与检索；资料删除后不再被召回，既有回答中的引用快照不改写。

当前 RAG 使用 SQLite FTS5 BM25，英文词项与中文双字词项检索；不是向量语义检索。查询返回最多 8 个原文段落，交给 Qwen 生成解释，模型的引用编号必须来自实际召回段落。未命中明确提示没有相关资料；模型失败或输出不可核对时仍展示已检索原文。没有把未配置的 Embedding 模型冒充为可用能力。

选择“只查知识库”可避免将构造事件当成知识依据；综合模式保留已有问题分流。设备分析的模型工具也经过 MCP，可检索资料辅助核查；真实现场数据仍需由采集系统接入。资料、标题和用户输入均作为不可信数据处理，不能改变工具权限。

## MCP

端点 `/agent-api/mcp`，实现 MCP **2025-11-25** 的无会话 Streamable HTTP JSON 响应模式：`initialize`、`notifications/initialized`、`ping`、`tools/list`、`tools/call`。通知返回 202 空响应；独立 GET 事件流返回 405。客户端 Accept 同时包含 application/json 与 text/event-stream。服务端验证 Origin、身份、参数和允许的工具；客户端禁止任意 URL 及重定向。

五个只读工具：

| 名称 | 作用 |
| --- | --- |
| list_events | 查询当前快照登记事件 |
| get_event | 读取事件数值证据 |
| get_runbook | 查询版本化通用核查清单 |
| search_knowledge | 检索当前工作空间资料原文 |
| get_knowledge_chunk | 按引用编号读取原文段落 |

真实部署中 Agent 使用回环 HTTP MCP 客户端执行握手、发现与工具调用，身份由服务端传入并校验。不是将普通内部函数改名为 MCP。页面“工具与连接”会进行实际握手后展示工具，回答可以展开调用记录。自动化单元测试中可注入相同协议的内存传输，HTTP 集成测试使用真正的回环连接。

外部 MCP 客户端连接网站 `/agent-api/mcp`：实测账号携带分配的 Bearer 令牌，通过 HTTPS 访问；匿名演练用随机 X-Fumai-Session，若要访问当前浏览器的资料，还需相同 X-Fumai-Client。不能把演示随机标识当作正式员工身份系统。没有新增设备控制、任意命令执行或自动邮件发送工具。

协议依据：[传输规范](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)、[工具规范](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)。检索依据：[SQLite FTS5](https://www.sqlite.org/fts5.html)。

## 存储与权限

仅新增 kb_documents、kb_chunks、kb_fts 及索引，不改写历史表。匿名演练按浏览器或会话隔离；实测模式下同站点资料共享，viewer 只读，operator 可上传、重试和删除。建库最多两个后台工作者；分析继续使用现有并发限制、持久化任务、SSE 进度与断线查询。建库任务重启后明确失败并保留原文件供重试，不无限挂起。

部署前用 SQLite backup API 备份；Nginx 仅将 agent-api 的请求上限从 16 KiB 改为 9 MiB，普通接口仍在后端限制 16 KiB。保留其他业务服务、数据库和通知配置。执行既有精确提交部署脚本，失败回退代码。

验收包括：中文故障码检索、原文引用、资料去重与删除、跨身份隔离、扫描件提示、模型伪造引用降级、真实 HTTP MCP 握手与工具调用、SSE、连续追问和既有会话回归。生产联调结果另记，不以模拟模型测试代替真实模型成功。
