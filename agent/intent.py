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
    if OPERATIONS.search(text):
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
        "help": "可以直接问“你是什么模型？”或“你能做什么？”。要分析运维线索，请选择一个事件，或输入“分析 inv_07 的异常证据”“同群证据不足时先查什么”。普通问答不会查询事件；只有运维问题才进入证据与模型工具流程。",
        "greeting": "你好，我是伏脉运维助手。你可以询问我的模型、功能和使用方法，也可以提供设备或事件来核查运维线索。",
        "out_of_scope": "我主要处理设备与光伏运维问题，也能介绍模型身份、功能和使用方法。请说明设备、事件、功率或需要核查的线索；当前问题不会触发设备证据分析。",
    }
    return {"response_type": "direct", "intent": intent, "summary": answers[intent],
            "engine": "direct", "model_status": "not_invoked", "model_name": name,
            "events": [], "evidence_ids": [], "trace": []}
