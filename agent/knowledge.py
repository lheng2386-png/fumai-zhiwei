"""Versioned general PV notes; never substitute for measurements of a device."""
import re


SOURCES = [
    {'id':'sma-stp-events','title':'SMA Sunny Tripower：事件原因与核查说明',
     'url':'https://manuals.sma.de/STPTL-30/en-US/391460235.html',
     'note':'电网低压、直流输入不足、温升和绝缘事件的通用参考；不同机型的告警码、阈值与处置规程不能直接通用。'},
    {'id':'sma-dc-start','title':'SMA Sunny Boy：直流启动条件未满足',
     'url':'https://manuals.sma.de/SBxx-1AV-41/en-US/11012315019.html',
     'note':'弱辐照、遮挡及组串设计/接线是直流启动条件核对方向，不是本站已确认原因。'},
]
SCOPE = '以下为光伏运维通用知识，不是对某台设备的现场诊断；具体告警含义和阈值以对应型号手册为准。'


def topic_for(message, previous=None):
    if re.search(r'欠压|低电压|电压偏低|低压|交直流|交流侧|直流侧|母线',message,re.I):return 'undervoltage'
    if re.search(r'过温|过热|温升|温度',message):return 'temperature'
    if re.search(r'绝缘|漏电|接地',message):return 'insulation'
    if re.search(r'mppt|最大功率',message,re.I):return 'mppt'
    if re.search(r'同群|残差|参考功率|阈值',message):return 'evidence_concepts'
    return (previous or {}).get('knowledge_topic') or 'pv_general'


def knowledge_context(turns):
    # The latest substantive subject wins; history/help does not erase a subject.
    for run in reversed(turns):
        if run.get('response_type')=='analysis':return None
        if run.get('knowledge_topic'):return run
        if run.get('events'):return None
    return None


def fallback(message, topic):
    if topic=='undervoltage':
        if re.search(r'清晨|早晨|早上|傍晚|日出|日落',message):
            return ('如果只在清晨或傍晚出现，要先区分“等待直流启动条件”和“交流电网欠压”。弱辐照、遮挡等可能使直流启动条件暂未满足；'
                    '但时间规律不能单独证明是正常启动。核对告警原文、持续时间、辐照变化及已有的直流/交流电压曲线，看日照增强后是否恢复、同站其他设备是否同步发生。'
                    '若日照正常仍持续或反复发生，再结合组串设计、接线与电网记录核查。没有型号和电压记录，不能确定原因。')
        if re.search(r'区分|区别|哪一侧|哪边|怎么判断|如何判断',message):
            return ('先看告警原文和型号手册中的监测点，不能只凭“欠压”两个字归类。直流/PV 输入欠压看组串或 MPPT 输入电压与启动/工作范围；'
                    '交流/电网欠压看并网点各相电压和电网事件；直流母线欠压是内部直流环节，不能等同于光伏输入或电网欠压。'
                    '把同一时刻已有的交流、直流曲线与告警时间对齐；若只有低功率记录，无法反推出哪一侧电压低。请补充告警原文或截图中的代码、机型及发生时段。')
        if re.search(r'依据|根据|为什么|证据',message) and not re.search(r'一般|常见|通常|原因',message):
            return ('这里的依据是厂家手册中对交流电网事件与直流启动事件的区分，不是读取了你现场的电压。'
                    'SMA 的事件说明把电网断开/并网点电压低列为交流侧核查方向，把弱辐照、遮挡和组串设计/接线列为直流输入或启动条件的核对方向。'
                    '因此必须先确认告警监测点；这些知识不能证明某台设备已经欠压，更不能仅凭低功率认定欠压。')
        return ('“欠压”先分清三种情况，不能直接归结为一种故障。\n'
                '直流/PV 输入侧：弱辐照、遮挡等可能使启动条件不足；组串设计与接线不匹配也需要结合机型和记录核对。\n'
                '交流/电网侧：并网点电压偏低、上级电网中断或交流线路/连接问题，是不同的可能方向。\n'
                '直流母线：属于逆变器内部直流环节，不能仅凭“母线欠压”确定是组件、电网还是内部器件问题。\n'
                '先收集型号、完整告警、发生时段和已有的交流/直流电压曲线，再判断属于哪一侧；仅有低功率不足以判断欠压。需要现场测量或接线检查时，由有资质人员按设备规程处理，不自行改保护阈值。')
    if topic=='temperature':
        return '过温或温度降额的通用核对方向包括环境温度、通风散热条件和散热部件状态；应先区分保护停机与功率降额，结合机型手册、已有温度曲线和告警时间判断。没有这些信息不能确认散热器或风扇故障。'
    if topic=='insulation':
        return '绝缘告警表示绝缘监测条件异常，可能涉及组件、直流电缆或连接部位及潮湿环境；应结合机型手册和现有监测记录核查，不能直接断定某一组件损坏。绝缘测量和接线处置需由有资质人员按隔离规程进行。'
    if topic=='mppt':
        return 'MPPT 是最大功率点跟踪：逆变器调整光伏侧工作点，以适应辐照和温度变化。MPPT 工作电压范围、启动电压和最大允许输入电压是不同指标，要按型号及组串设计分别核对，不能当成同一个阈值。'
    if topic=='evidence_concepts':
        if '同群' in message:return '同群证据是在相同时刻、可比条件下，将本机与其他设备对照；有效其他同群不含本机。数量不足时先核对可比性、缺测和时间对齐，不能据此确认故障。'
        if '残差' in message:return '残差描述实测与参考的差异，正负号取决于定义。持续差异可以形成核查线索，但不能单独证明具体故障原因。'
        return '参考功率是模型或规则给出的比较基准，实测功率是观测；阈值是筛查边界。它们用于定位候选偏离，不直接代表故障已确认或可挽回损失。'
    return '可以讨论光伏原理、逆变器告警和运维知识。请说明想了解的概念或告警现象；若涉及具体告警码、保护阈值或操作参数，还需要对应型号手册。当前通用知识资料不足以给出这个问题的具体结论。'


def answer(message, previous=None):
    topic=topic_for(message,previous)
    sources=SOURCES if topic=='undervoltage' else SOURCES[:1] if topic in {'temperature','insulation'} else []
    return {'knowledge_topic':topic,'knowledge_sources':sources,'answer':fallback(message,topic),
            'knowledge_scope':SCOPE,'knowledge_question':(previous or {}).get('knowledge_question') or message}


def guard_knowledge(analysis, topic):
    if not analysis.get('answers'):raise ValueError('Knowledge answer omitted questions')
    text='\n'.join(a['answer'] for a in analysis['answers'])
    if analysis.get('evidence_ids') or any(a['evidence_ids'] for a in analysis['answers']):
        raise ValueError('General knowledge cannot invent device evidence')
    if re.search(r'已(?:确认|检测到|测得)|本站(?:存在|发生)|该设备(?:存在|发生|已经)|确定是.{0,12}(?:故障|欠压)',text):
        raise ValueError('General knowledge cannot confirm a site condition')
    if re.search(r'(?<!\w)\d+(?:\.\d+)?\s*(?:[vV]|伏|欧姆|Ω|兆欧)',text):
        raise ValueError('Device-specific thresholds require a supplied manual')
    if topic=='undervoltage' and not re.search(r'交流|直流|监测点',text):
        raise ValueError('Undervoltage needs the monitored side clarified')
