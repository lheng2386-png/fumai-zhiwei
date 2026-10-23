"""Deterministic routing before evidence access, model calls or worker admission."""
import re
import unicodedata

from .core import InputError


PATTERNS = {
    "identity": [
        r"^(?:模型(?:身份|名称|版本)|什么模型|哪个模型|使用什么模型|用的什么模型)[?？。!！]*$",
        r"(?:你|您|当前|现在|助手|底层|背后).{0,25}(?:什么|哪个|哪种|哪款|哪一|名称|型号|名字|版本).{0,10}(?:模型|model|qwen)",
        r"(?:你|您|当前|现在|助手|底层|背后).{0,18}(?:模型|model).{0,14}(?:是什么|是哪个|叫什么|名称|型号|版本)",
        r"^(?:请问)?(?:你|您)(?:到底)?(?:是谁|叫什么(?:名字)?|是什么|是(?:ai|人工智能|机器人)吗)[?？。!！]*$",
        r"(?:你|您)是(?:qwen|通义|千问|gpt|chatgpt).{0,8}(?:吗|么|[?？])",
        r"\b(?:what|which)\b.{0,35}\bmodel\b.{0,25}\b(?:you|using|use|are|is)\b",
        r"\bwho\s+are\s+you\b|\byour\s+(?:name|model|identity)\b",
    ],
    "capabilities": [
        r"(?:你|您|助手).{0,10}(?:能做什么|可以做什么|能干什么|有什么功能|有哪些功能|能帮我什么|能帮我做什么|支持哪些功能|支持什么功能|能分析哪些|可以分析哪些)",
        r"功能介绍|介绍.{0,4}(?:功能|你自己|一下自己)|^功能[?？]*$",
        r"\b(?:what can you do|your (?:features|capabilities)|capabilities|what do you do)\b",
    ],
    "help": [
        r"(?:怎么|如何)(?:使用|用)(?:你|您|这个(?:系统|助手)|本(?:系统|助手))|(?:使用|操作)(?:说明|指南|帮助)",
        r"^(?:帮助|帮助一下|怎么用|如何使用|help(?:\s+me)?)[?？。!！]*$",
        r"\bhow (?:do i|to) use (?:you|this (?:system|assistant))\b",
    ],
}
OPERATIONS = re.compile(
    r"设备|逆变器|电站|光伏|事件|功率|发电|异常|故障|告警|核查|复核|排查|同群|辐照|限发|检修|工单|证据"
    r"|inv[_ -]?[a-z0-9]+|demo-2107-[a-z0-9-]+"
    r"|\b(?:device|inverter|event|power|anomaly|anomalies|fault|alarm|inspect|investigate|maintenance)\b"
)
TASK_PATTERNS = {
    'history': r'(?:分析|诊断|对话|聊天|会话).{0,6}(?:记录|历史)|(?:历史|之前|以前|刚才|上次).{0,16}(?:分析记录|分析过|有没有分析|是否分析|分析报告|分析结果)|(?:查询|查看|列出|查找).{0,12}(?:历史|之前|以前|最近|过往).{0,10}(?:分析|诊断|结果)|(?:刚才|上次|之前).{0,12}(?:分析(?:了|的是)|诊断(?:了|的是))|(?:分析|诊断)(?:过|了吗)|previous analys|analysis history',
    'correction': r'不对|纠正|更正|你误解|我说的是|不是这样|说错了|不是.{0,35}(?:而是|是inv|是 inv|改为)',
    'business': r'负责人|负责谁|谁负责|设备(?:当前)?状态|逆变器(?:当前)?状态|短信|邮件|通知.*(?:状态|发送|成功|失败)|发电量|预测电量|实际电量|预测偏差|运行日报|运维总结|复核进度|复核状态|工单进度',
    'knowledge': r'(?:什么是|含义|定义|是什么意思|如何理解|科普|原理|区别).{0,25}(?:同群|残差|功率|阈值|故障|告警|光伏|逆变器|mppt|欠压|绝缘)|(?:光伏|逆变器|欠压|过压|过温|过热|绝缘|mppt|组串|辐照|遮挡|交直流|交流侧|直流侧|母线).{0,40}(?:一般|常见|通常|原理|区别|什么意思|是什么|为什么|哪些原因|什么原因|怎么区分|如何区分)|(?:一般|常见|通常|为什么|怎么|如何|解释|介绍|讲讲).{0,35}(?:欠压|过压|过温|过热|绝缘|mppt|组串|辐照|遮挡)|(?:同群|残差|功率|阈值).{0,15}(?:是什么|什么意思)|同群证据不足时.{0,10}(?:先查什么|怎么办|如何处理)',
    'followup': r'刚才|上一条|上一个|上次|你说的|这样判断|这么判断|为什么这样|为什么这么|继续解释|进一步解释|展开(?:说|讲|解释)|详细(?:说|讲|解释)|该设备|这台设备|这个结论|这条证据|这条事件|它的|(?:判断|结论|你说|你认为|这么说).{0,12}(?:依据|根据|证据|理由)|(?:依据|根据|证据|理由).{0,8}(?:什么|哪些|哪里|哪来)|(?:有什么|有哪些|给出|说说).{0,8}(?:依据|根据|证据|理由)|凭什么|^为什么(?:呢)?[?？]*$|^继续[?？]*$|为什么.{0,15}(?:通信|缺失|排序)|(?:通信|缺失).{0,20}(?:依据|证据|排在|第一|首位)',
    'evidence_query': r'(?:实测|参考|功率|同群|采样).{0,15}(?:多少|几台|几条|多长|差值)|(?:多少|几台).{0,12}(?:同群|功率)|(?:是|属于).{0,10}(?:单机|共性)',
}
ANALYSIS = re.compile(r'分析|诊断|核查|排查|检查|核对|回顾|对比|比较|异常|故障|偏低|偏高|不一致|偏差|超限|\b(?:analy[sz]e|check|inspect|investigate)\b')


def normalized(message):
    if not isinstance(message, str) or not 1 <= len(message.strip()) <= 2000:
        raise InputError("请输入 1–2000 个字符的问题")
    return unicodedata.normalize("NFKC", message).strip().lower()


def informational_intent(text):
    for intent, patterns in PATTERNS.items():
        if any(re.search(pattern, text) for pattern in patterns):
            return intent
    return None


def classify_question(message, has_context=False):
    text = normalized(message)
    direct = informational_intent(text)
    if direct:
        # A separate maintenance request can accompany an identity/help question.
        clauses = re.split(r"[,，?？。;；\n]+|顺便|另外|同时|然后", text)
        if any(OPERATIONS.search(clause) and not informational_intent(clause) for clause in clauses):
            return "maintenance"
        return direct
    for intent, pattern in TASK_PATTERNS.items():
        if re.search(pattern,text):
            if (intent=='knowledge' and has_context and re.search(r'依据|根据|凭什么|证据.{0,8}(?:什么|哪|支持)|为什么(?:这样|这么)判断',text)
                    and not re.search(r'一般|通常|常见|科普',text)):
                return 'followup'
            if intent == 'followup' and re.search(r'重新分析|再次分析|再分析|完整诊断|完整报告|^(?:请)?分析',text):
                return 'maintenance'
            return intent
    if has_context and re.search(r'^(?:那|如果|要是|假如|为什么|怎么|如何|能否|是不是|也就是说|接下来|还需要|(?:请)?(?:解释|展开|详细说明)|能.{0,3}(?:解释|展开|详细)|你.{0,5}(?:判断|依据|认为))|(?:数据|证据).{0,10}支持.{0,10}(?:结论|判断)|(?:解释|说明|告诉|给出|说说).{0,10}(?:依据|理由)',text):
        return 'followup'
    if OPERATIONS.search(text) and (ANALYSIS.search(text) or re.fullmatch(r'(?:inv[_ -]?\d+|demo-2107-[a-z0-9-]+)',text)):
        return "maintenance"
    if has_context and re.search(r"分析|看看|查看|检查|核对|查一下|看一下|继续|\b(?:analyze|check)\b", text):
        return "maintenance"
    if re.fullmatch(r"(?:你好|您好|嗨|hello|hi|谢谢|感谢)[!！。,.，?？\s]*", text):
        return "greeting"
    return "out_of_scope"


def direct_answer(intent, model=None):
    name = getattr(model, "model", None)
    name = name if isinstance(name, str) and name else None
    identity = "我是伏脉运维助手。"
    if name:
        identity += f"当前配置的大模型是 {name}。这条身份说明由系统直接回答，没有调用模型或设备证据工具。"
    elif model is None:
        identity += "当前未接入大模型，运维分析使用本地规则与证据工具。"
    else:
        identity += "模型服务已配置，当前适配器没有提供可公开的模型名称。"
    answers = {
        "identity": identity,
        "capabilities": "我可以查询已登记的设备和异常事件、解释功率及同群证据、提供核查路径，并生成需人工确认的核查草稿。你也可以直接询问模型身份、功能和使用方法。设备操作和外部派单不在我的权限内。",
        "help": "可以问“逆变器欠压一般是什么原因？”等光伏知识，也可以对上一轮回答追问“判断依据是什么？”“能展开解释吗？”。需要新诊断时，请选择事件或输入“分析 inv_07 的异常证据”；历史可问“列出之前的设备分析记录”。",
        "greeting": "你好，我是伏脉运维助手。你可以询问我的模型、功能和使用方法，也可以提供设备或事件来核查运维线索。",
        "out_of_scope": "我可以回答光伏原理、逆变器告警及运维知识，也可以解释本会话的分析依据。请补充具体问题或你想追问的结论；例如“逆变器欠压一般是什么原因？”或“刚才的判断依据是什么？”。",
    }
    return {"response_type": "direct", "intent": intent, "summary": answers[intent],
            "engine": "direct", "model_status": "not_invoked", "model_name": name,
            "events": [], "evidence_ids": [], "trace": []}
