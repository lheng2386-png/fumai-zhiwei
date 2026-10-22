/* Keep routing parity with agent/intent.py; verified by shared intent_cases.json. */
(() => {
    'use strict';
    const rules = {"information": {"identity": ["^(?:模型(?:身份|名称|版本)|什么模型|哪个模型|使用什么模型|用的什么模型)[?？。!！]*$", "(?:你|您|当前|现在|助手|底层|背后).{0,25}(?:什么|哪个|哪种|哪款|哪一|名称|型号|名字|版本).{0,10}(?:模型|model|qwen)", "(?:你|您|当前|现在|助手|底层|背后).{0,18}(?:模型|model).{0,14}(?:是什么|是哪个|叫什么|名称|型号|版本)", "^(?:请问)?(?:你|您)(?:到底)?(?:是谁|叫什么(?:名字)?|是什么|是(?:ai|人工智能|机器人)吗)[?？。!！]*$", "(?:你|您)是(?:qwen|通义|千问|gpt|chatgpt).{0,8}(?:吗|么|[?？])", "\\b(?:what|which)\\b.{0,35}\\bmodel\\b.{0,25}\\b(?:you|using|use|are|is)\\b", "\\bwho\\s+are\\s+you\\b|\\byour\\s+(?:name|model|identity)\\b"], "capabilities": ["(?:你|您|助手).{0,10}(?:能做什么|可以做什么|能干什么|有什么功能|有哪些功能|能帮我什么|能帮我做什么|支持哪些功能|支持什么功能|能分析哪些|可以分析哪些)", "功能介绍|介绍.{0,4}(?:功能|你自己|一下自己)|^功能[?？]*$", "\\b(?:what can you do|your (?:features|capabilities)|capabilities|what do you do)\\b"], "help": ["(?:怎么|如何)(?:使用|用)(?:你|您|这个(?:系统|助手)|本(?:系统|助手))|(?:使用|操作)(?:说明|指南|帮助)", "^(?:帮助|帮助一下|怎么用|如何使用|help(?:\\s+me)?)[?？。!！]*$", "\\bhow (?:do i|to) use (?:you|this (?:system|assistant))\\b"]}, "operations": "设备|逆变器|电站|光伏|事件|功率|发电|异常|故障|告警|核查|复核|排查|同群|辐照|限发|检修|工单|证据|inv[_ -]?[a-z0-9]+|demo-2107-[a-z0-9-]+|\\b(?:device|inverter|event|power|anomaly|anomalies|fault|alarm|inspect|investigate|maintenance)\\b", "tasks": {"history": "(?:分析|诊断|对话|聊天|会话).{0,6}(?:记录|历史)|(?:历史|之前|以前|刚才|上次).{0,16}(?:分析记录|分析过|有没有分析|是否分析|分析报告|分析结果)|(?:查询|查看|列出|查找).{0,12}(?:历史|之前|以前|最近|过往).{0,10}(?:分析|诊断|结果)|(?:刚才|上次|之前).{0,12}(?:分析(?:了|的是)|诊断(?:了|的是))|(?:分析|诊断)(?:过|了吗)|previous analys|analysis history", "correction": "不对|纠正|更正|你误解|我说的是|不是这样|说错了|不是.{0,35}(?:而是|是inv|是 inv|改为)", "business": "负责人|负责谁|谁负责|设备(?:当前)?状态|逆变器(?:当前)?状态|短信|邮件|通知.*(?:状态|发送|成功|失败)|发电量|预测电量|实际电量|预测偏差|运行日报|运维总结|复核进度|复核状态|工单进度", "knowledge": "(?:什么是|含义|定义|是什么意思|如何理解).{0,25}(?:同群|残差|功率|阈值|故障|告警|光伏|逆变器)|(?:同群|残差|功率|阈值).{0,15}(?:是什么|什么意思)|同群证据不足时.{0,10}(?:先查什么|怎么办|如何处理)", "followup": "刚才|上一条|上一个|上次|你说的|这样判断|为什么这样|继续解释|进一步解释|该设备|这台设备|这个结论|这条证据|这条事件|它的|依据是什么|凭什么|^为什么(?:呢)?[?？]*$|^继续[?？]*$|为什么.{0,15}(?:通信|缺失|排序)|(?:通信|缺失).{0,20}(?:依据|证据|排在|第一|首位)", "evidence_query": "(?:实测|参考|功率|同群|采样).{0,15}(?:多少|几台|几条|多长|差值)|(?:多少|几台).{0,12}(?:同群|功率)|(?:是|属于).{0,10}(?:单机|共性)"}, "analysis": "分析|诊断|核查|排查|检查|核对|回顾|对比|比较|异常|故障|偏低|偏高|不一致|偏差|超限|\\b(?:analy[sz]e|check|inspect|investigate)\\b"};
    const operation = new RegExp(rules.operations);
    const info = text => Object.entries(rules.information).find(([, patterns]) => patterns.some(pattern => new RegExp(pattern).test(text)))?.[0] || null;
    function classify(question, hasContext = false) {
        const text = question.normalize('NFKC').trim().toLowerCase();
        const direct = info(text);
        if (direct) {
            const clauses = text.split(/[,，?？。;；\n]+|顺便|另外|同时|然后/);
            return clauses.some(clause => operation.test(clause) && !info(clause)) ? 'maintenance' : direct;
        }
        for(const [intent,pattern] of Object.entries(rules.tasks)) {
            if(new RegExp(pattern).test(text)) {
                if(intent==='followup'&&/重新分析|再次分析|再分析|完整诊断|完整报告|^(?:请)?分析/.test(text))return 'maintenance';
                return intent;
            }
        }
        if(hasContext&&/^(?:那|为什么|怎么|如何|能否|是不是|也就是说|接下来|还需要)/.test(text))return 'followup';
        if(operation.test(text)&&(new RegExp(rules.analysis).test(text)||/^(?:inv[_ -]?\d+|demo-2107-[a-z0-9-]+)$/.test(text))) return 'maintenance';
        if (hasContext && /分析|看看|查看|检查|核对|查一下|看一下|继续|\b(?:analyze|check)\b/.test(text)) return 'maintenance';
        return /^(?:你好|您好|嗨|hello|hi|谢谢|感谢)[!！。,.，?？\s]*$/.test(text) ? 'greeting' : 'out_of_scope';
    }
    function offlineAnswer(intent) {
        const answers = {
            identity: '我是伏脉运维助手。当前是离线样例预览，未调用模型，无法确认服务器当前配置的模型。',
            capabilities: '我可以查询已登记的设备和异常事件、解释功率及同群证据、提供核查路径，并生成需人工确认的核查草稿。当前离线预览只使用样例，不能生成服务端草稿。',
            help: '可以问“你是什么模型？”或“你能做什么？”。分析运维线索时，请选择事件或输入“分析 inv_07 的异常证据”。当前离线预览不调用模型，也不保存服务端记录。',
            greeting: '你好，我是伏脉运维助手。可以询问模型、功能、使用方法，或提供设备和事件来核查运维线索。',
            out_of_scope: '我主要处理设备与光伏运维问题，也能介绍模型身份、功能和使用方法。请提供设备、事件、功率或需要核查的线索；当前问题不会触发样例证据分析。',
        };
        return { response_type: 'direct', intent, summary: answers[intent] || '这个问题需要连接服务端的个人历史或会话上下文。离线预览不会重新执行设备分析，也无法确认历史或业务数据。', engine: 'direct', model_status: 'not_invoked', model_name: null, events: [], evidence_ids: [], trace: [] };
    }
    function validDirect(result) {
        return result?.response_type === 'direct' && ['identity','capabilities','help','greeting','out_of_scope'].includes(result.intent) &&
            result.engine === 'direct' && result.model_status === 'not_invoked' && typeof result.run_id === 'string' && typeof result.summary === 'string' &&
            Array.isArray(result.events) && !result.events.length && Array.isArray(result.evidence_ids) && !result.evidence_ids.length && Array.isArray(result.trace) && !result.trace.length;
    }
    function renderDirect(result, esc, glyph) {
        return `<article class="agent-message assistant"><span class="agent-message-avatar">${glyph}</span><div class="agent-answer agent-direct-answer"><div class="agent-answer-meta"><strong>伏脉运维助手</strong><span>直接回答</span></div><p class="agent-answer-summary" style="white-space:pre-wrap">${esc(result.summary)}</p>${result.run_id ? `<details class="agent-audit"><summary>查看对话记录</summary><p>记录编号：${esc(result.run_id)}</p></details>` : ''}</div></article>`;
    }
    globalThis.FumaiIntent = Object.freeze({ classify, offlineAnswer, validDirect, renderDirect });
})();
