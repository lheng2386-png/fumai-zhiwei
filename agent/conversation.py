"""Owner-scoped planning and answers grounded in stored turns and snapshot facts."""
import copy
import re

from .core import InputError, RUNBOOKS
from .intent import classify_question, direct_answer, normalized
from .knowledge import answer as knowledge_answer, knowledge_context


def questions(message):
    normalized(message)
    parts = [p.strip(' ,，。?？;；\n') for p in re.split(r'[?？;；\n]+|另外|还有|同时(?:请)?|再请|以及(?:请)?|并(?=查询|列出|解释|说明)|(?<!\d)[1-8][.、](?!\d)\s*|[，,](?=请|为什么|是否|有没有|负责人|短信|实际|预测|列出|解释|什么是)',message)]
    parts = [p for p in parts if p]
    if len(parts)>8:
        raise InputError('一次最多提出 8 个子问题，请分批查询')
    return parts or [message]


def device_ids(message):
    return list(dict.fromkeys('inv_'+str(int(n)).zfill(2) for n in re.findall(r'\binv[_ -]?(\d{1,3})\b',message,re.I)))


def dedupe(items):
    found=set();result=[]
    for text in items:
        key=re.sub(r'[\s，,。.!！;；]+','',text)
        if key and key not in found:
            found.add(key);result.append(text)
    return result


def fact_rows(events):
    return [{'event_id':e['event_id'],'device_id':e['inverter'],
             'text':f"{e['inverter']} 在 {e['start_local']} 的事件：实测均值 {e['mean_actual_kw']} kW，模型参考功率均值 {e['mean_reference_kw']} kW；有效其他同群 {e['min_peer_count']} 台。",
             'fields':['mean_actual_kw','mean_reference_kw','min_peer_count','start_local']} for e in events]


def context_excerpt(run):
    return {'question':run['question'][:1000],
            'summary':(run.get('model_analysis',{}).get('summary') or run.get('summary',''))[:1000],
            'answers':[{'question':a['question'][:200],'answer':a['answer'][:500]} for a in run.get('answers',[])[:4]],
            'checks':run.get('checks',[])[:6],
            'history_records':[{'question':h['question'][:200],'run_id':h['run_id']} for h in run.get('history_records',[])[:5]],
            'user_feedback':run.get('user_feedback')}


def guard_model(analysis, events):
    """Reject unsupported causal ranking and positive missing-data assertions."""
    if not analysis:
        return
    values=[analysis.get('summary',''),*analysis.get('hypotheses',[])]
    values += [a.get('answer','') for a in analysis.get('answers',[])]
    known={'kw':set(),'kwh':set(),'台':set(),'个采样':set()}
    for event in events:
        known['kw'].update(round(float(event[k]),2) for k in ['mean_actual_kw','mean_reference_kw'])
        known['kw'].add(round(event['mean_reference_kw']-event['mean_actual_kw'],2))
        known['kwh'].add(round(event['reference_shortfall_kwh'],2))
        known['台'].add(float(event['min_peer_count']));known['个采样'].add(float(event['samples']))
    for value in values:
        if re.search(r'(?:无|没有|未提供|缺少).{0,60}(?:通信|告警|限发|日志|记录).{0,60}(?<!不能)(?<!无法)(?<!不应)(?:进一步佐证|从而证明|因此证明|反而证明)',value):
            raise ValueError('Absent records cannot serve as positive evidence')
        for number,unit in re.findall(r'(?<![\d.])(-?\d+(?:\.\d+)?)\s*(kWh|kW|台|个采样)',value,re.I):
            if not any(abs(float(number)-n)<.06 for n in known[unit.lower() if unit.lower() in {'kw','kwh'} else unit]):
                raise ValueError('A model number has no matching observed field')
        for clause in re.split(r'[。；;\n，,]',value):
            if re.search(r'同群(?:参考值|均值|平均功率|参考功率)',clause) and not re.search(r'未提供|没有|未知|不能|无法',clause):
                raise ValueError('Model reference power is not a measured peer mean')
            denial = re.search(r'不能|无法|不足|不支持|未能|没有.*依据|尚无|不应|不作|无排序|尚未|未提供|未知|是否',clause)
            uncertain = denial or re.search(r'待核查|待确认|需核查|需要核查|可能',clause)
            ranking=r'首要原因|最可能|排在首位|排(?:在)?第一|优先认定|第一(?:原因|位)|主要原因是|原因确定|(?:通信|限发|遮挡|数据).{0,8}(?:优先考虑|概率最高|可能性最大|可能性较大)'
            denied_rank=re.search(r'(?:不能|无法|不应|没有依据|无依据|不足以|尚不能).{0,20}(?:排序|排名|确定|认定|最可能|首要|排在|第一)|(?:排序|排名|最可能|首要).{0,8}(?:没有|无|缺少).{0,5}依据',clause)
            if re.search(ranking,clause) and not denied_rank:
                raise ValueError('Unsupported cause ranking')
            if re.search(r'(?:存在|出现|发现|发生|有).{0,8}(?:数据缺失|采样缺失|丢包|通信中断|通信故障)|数据(?:明显)?缺失|通信(?:已经|发生了)异常',clause) and not uncertain:
                # The current snapshot contract contains no packet/gap telemetry.
                raise ValueError('Missingness is not an observed snapshot fact')
            conditional=re.search(r'(?:排除|核对|核查|核实|检查|验证|是否|可能).{0,25}(?:通信|限发|遮挡|故障|数据缺失)',clause)
            if (re.search(r'(?:已确认|确认(?:为|是)|确定(?:为|是)|确诊|原因是).{0,20}(?:故障|通信|遮挡|限发|设备异常|检修)',clause) and not denial):
                raise ValueError('The snapshot does not confirm a fault cause')
            if (re.search(r'(?:限发|遮挡|通信故障|通信异常|数据缺失).{0,15}(?:导致|引起|造成)',clause)
                    and not uncertain and not conditional):
                raise ValueError('The snapshot does not confirm a fault cause')


class Conversation:
    def __init__(self,snapshot,store):
        self.snapshot,self.store=snapshot,store

    def context(self,owner,conversation_id):
        turns=self.store.turns(owner,conversation_id,limit=16)
        reference=next((r for r in reversed(turns) if r.get('response_type')=='analysis' or r.get('events') and r.get('response_type')=='answer'),None)
        if reference is None:
            earlier=self.store.history(owner,conversation_id=conversation_id,analysis_only=True,limit=1)
            if earlier:reference=self.store.get_run(owner,earlier[0]['run_id'])
        return turns,reference

    def routes(self,owner,body):
        conversation=body.get('_conversation_id') or self.store.default_conversation(owner)
        turns,_=self.context(owner,conversation)
        return [(q,classify_question(q,bool(turns or body.get('event_id') or body.get('device_id')))) for q in questions(body.get('message'))]

    def needs_model(self,owner,body):
        return any(kind=='maintenance' for _,kind in self.routes(owner,body)) or self.can_enrich_reply(owner,body)

    def can_enrich_reply(self,owner,body):
        turns,reference=self.context(owner,body.get('_conversation_id') or self.store.default_conversation(owner))
        routes=self.routes(owner,body)
        return (any(kind=='knowledge' for _,kind in routes)
                or bool(knowledge_context(turns)) and any(kind=='followup' and not device_ids(q) and not re.search(r'设备分析|分析报告|设备的|这台|事件',q) for q,kind in routes)
                or bool(reference and reference.get('events')) and any(kind=='followup' and not re.search(r'通信|缺失|排序|首位|第一|原因|欠压|电压|为什么.*先|而不是',q) for q,kind in routes))

    def direct(self,owner,body,model=None,reference_override=None):
        conversation=body.get('_conversation_id') or self.store.default_conversation(owner)
        turns,reference=self.context(owner,conversation)
        reference=reference_override or reference
        routes=self.routes(owner,body)
        if len(routes)==1 and routes[0][1] in {'identity','capabilities','help','greeting','out_of_scope'}:
            return direct_answer(routes[0][1],model)
        answers=[];records=[];referenced=[];facts=[];feedback=None;collected={};sources={};knowledge=None;knowledge_qids=[];knowledge_notes=[]
        previous_knowledge=knowledge_context(turns)
        for number,(question,kind) in enumerate(routes,1):
            text=normalized(question);devices=device_ids(question)
            knowledge_follow=kind=='followup' and previous_knowledge and not devices and not re.search(r'设备分析|分析报告|设备的|这台|事件',text)
            if not devices and body.get('event_id') and (kind=='evidence_query' or kind in {'history','business'} and re.search(r'该设备|这台|它',text)):
                devices=[self.snapshot.event(body['event_id'])['inverter']]
            correction=next((r.get('user_feedback') for r in reversed(turns) if r.get('user_feedback',{}).get('device_ids')),None)
            if not knowledge_follow and not devices and correction and kind in {'followup','evidence_query'}:
                devices=correction['device_ids'][-1:]
            if not knowledge_follow and not devices and reference and (kind in {'followup','evidence_query'} or kind=='history' and re.search(r'该设备|这台|它',text)):
                devices=reference.get('target_device_ids') or list(dict.fromkeys(e['inverter'] for e in reference.get('events',[])))
            cited=[];status='answered'
            if kind=='history':
                # "刚才" is conversation-local; unqualified history is owner-wide.
                scope=conversation if re.search(r'刚才|本次会话|当前会话|这个会话',text) else None
                rows=self.store.history(owner,conversation_id=scope,device_id=devices[0] if len(devices)==1 else None,analysis_only=True,limit=20)
                records.extend(rows)
                target='、'.join(devices) if devices else '设备'
                answer=f"找到 {len(rows)} 条可查看的{target}分析记录（最多展示最近 20 条）。" if rows else f"在你有权访问的{'当前会话' if scope else '个人历史'}中未找到{target}分析记录。"
                if re.search(r'是否|有没有|分析过|了吗',text):
                    answer=('是，' if rows else '没有查到，')+answer
                if rows:
                    answer+='这些是已保存的分析，不会重新执行设备诊断。'
            elif kind=='business':
                if re.search(r'复核(?:进度|状态)',text) and reference:
                    rows=[e for e in reference['events'] if not devices or e['inverter'] in devices]
                    answer='；'.join(f"{e['event_id']}：快照中的复核状态为 {e['review_status']}" for e in rows)+ '。这是分析所引用快照中的状态，尚不能代替共享业务库的当前进度。'
                    cited=[e['event_id'] for e in rows]
                else:
                    service='设备负责人/人员资料' if '负责' in text else '设备当前运行状态' if re.search(r'设备.*状态|逆变器.*状态',text) else '短信和邮件投递状态' if re.search(r'短信|邮件|通知',text) else '实际/预测发电量与偏差' if re.search(r'发电|电量|预测|实际',text) else '人工复核及工单进度' if re.search(r'复核|工单',text) else '运行日报和运维总结'
                    answer=f"{service}的业务数据尚未接入本智能体，当前无法查询。"
                    if re.search(r'发电|电量|预测',text):
                        answer+='运行异常分数、参考电量差均不能作为预测发电量或实际发电量。'
                    status='not_connected'
            elif kind=='correction':
                feedback={'text':question,'source':'user_feedback','verified':False,'device_ids':device_ids(question)}
                answer='已记录这条纠正，后续追问会携带你的说明；它是用户反馈，不会直接改写设备观测或确认故障。'
                if re.search(r'通信|缺失|排序|第一|首位',text):
                    answer+='当前快照没有通信日志或缺失采样明细，不能把通信异常或数据缺失当成已知事实，也没有依据给故障原因排序。'
                if not reference:
                    answer+='当前会话还没有可引用的设备分析，请说明设备或先打开一条自己的历史分析。'
            elif kind=='knowledge':
                knowledge=knowledge_answer(question)
                answer=knowledge['answer'];status='insufficient' if knowledge['knowledge_topic']=='pv_general' else 'answered'
                knowledge_qids.append(str(number));knowledge_notes.append({'question_id':str(number),**knowledge})
            elif kind=='evidence_query':
                rows=[e for e in (reference or {}).get('events',[]) if not devices or e['inverter'] in devices]
                if not rows and devices:
                    rows=self.snapshot.list_events(devices[0],limit=3)['events']
                if rows:
                    answer='；'.join(f['text'] for f in fact_rows(rows)[:3])
                    if re.search(r'单机|共性',text):
                        labels={'device_specific_candidate':'单机偏离候选','shared_or_model_candidate':'同群共性变化候选','insufficient_peers':'同群证据不足'}
                        answer+=' 证据类别：'+'、'.join(dict.fromkeys(labels[e['evidence']] for e in rows))+'；这些类别不等于确认故障。'
                    cited=[e['event_id'] for e in rows];facts.extend(fact_rows(rows))
                    # Snapshot lookups are explicitly tagged instead of masquerading as a past analysis.
                    if not reference or not all(e['event_id'] in reference.get('evidence_ids',[]) for e in rows):
                        reference={'events':rows,'metadata':self.snapshot.meta,'run_id':None}
                else:
                    answer='请说明设备或事件。当前没有可供此问题引用的设备观测，不能凭空填写功率或同群数量。';status='insufficient'
            elif kind=='followup':
                if knowledge_follow:
                    knowledge=knowledge_answer(question,previous_knowledge)
                    answer=knowledge['answer'];status='insufficient' if knowledge['knowledge_topic']=='pv_general' else 'answered'
                    knowledge_qids.append(str(number));knowledge_notes.append({'question_id':str(number),**knowledge})
                elif not reference:
                    answer='当前会话没有可以对应“刚才/上一条”的设备分析。请先分析设备，或打开自己的一条历史分析后再追问。';status='insufficient'
                else:
                    rows=[e for e in reference['events'] if not devices or e['inverter'] in devices]
                    if not rows:
                        answer='上一轮分析没有该设备的证据，请明确要新分析的设备；我不会把其他设备的证据移到这里。';status='insufficient'
                    else:
                        cited=[e['event_id'] for e in rows]
                        referenced.extend(reference.get('source_run_ids') or ([reference['run_id']] if reference.get('response_type')=='analysis' and reference.get('run_id') else []))
                        facts.extend(fact_rows(rows))
                        if re.search(r'欠压|电压',text):
                            answer='；'.join(f['text'] for f in fact_rows(rows)[:2])+' 上一轮只有功率和同群观测，没有对应的电压测点或欠压告警记录。因此不能把候选功率偏离判断为欠压；需要完整告警、机型及同一时刻的交流/直流电压记录来验证。'
                        elif re.search(r'通信|缺失|排序|第一|首位|原因',text):
                            answer='；'.join(f['text'] for f in fact_rows(rows)[:2])+' 此前证据只支持观测到的功率/同群差异，没有通信日志或缺失采样明细。因此不能确认通信故障、声称已发现数据缺失，或对故障原因强行排序。'
                        elif re.search(r'为什么.*(?:先|而不是|优先)',text):
                            answer='此前清单是信息核对步骤，不是原因概率排名。当前没有限发指令或检修记录，不能断定某个原因更可能；应根据资料可得性并行核对运行安排和维护背景，再用独立记录验证。'
                        elif re.search(r'先|下一步|怎么|如何|建议',text):
                            checks=dedupe(c for e in rows for c in RUNBOOKS[e['evidence']]['checks'])
                            answer='基于上一轮证据，建议先核对运行背景和现场记录；'+('；'.join(checks[:2]))+' 这些是核查建议，不是已发现的故障。'
                        else:
                            answer='判断依据来自上一轮保存的事件观测：'+ '；'.join(f['text'] for f in fact_rows(rows)[:2])+'\n判断链条是：实测与模型参考存在差异，再结合事件的同群对照类别形成候选线索。'
                            labels={'device_specific_candidate':'单机偏离候选','shared_or_model_candidate':'同群共性/模型偏离候选','insufficient_peers':'同群证据不足'}
                            answer+='本次类别为'+ '、'.join(dict.fromkeys(labels[e['evidence']] for e in rows))+'；模型参考不是同群实测均值，候选异常也不等于已确认故障。'
                        if any(r.get('user_feedback') for r in turns):
                            answer+=' 已同时保留你在本会话中的纠正；未验证的反馈与观测事实分开处理。'
            else:
                answer=direct_answer(kind,model)['summary'] if kind!='maintenance' else '该子问题需要新的设备分析，请明确设备或选择事件。'
            if cited:
                for eid in cited:
                    event=next((e for e in (reference or {}).get('events',[]) if e['event_id']==eid),None) or self.snapshot.events.get(eid)
                    if event:
                        collected[eid]=copy.deepcopy(event)
                        meta=(reference or {}).get('metadata') or self.snapshot.meta
                        sources[eid]={'event_id':eid,**{k:meta.get(k) for k in ['source','dataset_version','observed_at','fingerprint']}}
            answers.append({'question_id':str(number),'question':question,'intent':kind,'status':status,'answer':answer,'evidence_ids':cited})
        unique_records={r['run_id']:r for r in records}
        events=list(collected.values())
        result={'response_type':'answer','intent':routes[0][1] if len(routes)==1 else 'compound','summary':answers[0]['answer'] if len(answers)==1 else '已逐项回答；无法取得的业务数据已明确标注。',
                'answers':answers,'coverage':{'requested':len(routes),'answered':len(answers),'unanswered':[]},'history_records':list(unique_records.values()),
                'events':events,'evidence_ids':[e['event_id'] for e in events],'facts':list({f['event_id']:f for f in facts}.values()),
                'evidence_sources':list(sources.values()),
                'source_run_ids':list(dict.fromkeys(referenced)),'trace':[],'engine':'conversation','model_status':'not_invoked','model_name':getattr(model,'model',None),
                'boundary':'事实、待核查假设和未知信息分别展示；历史查询与追问不会重新执行完整诊断。'}
        if reference and events:
            result['metadata']=reference['metadata']
            result['limitations']=['当前观测不足以确认具体故障原因；未提供的通信、告警和调度/维修记录不能用于证明或排除某种原因。']
        if feedback:result['user_feedback']=feedback
        if knowledge:
            result.update({k:v for k,v in knowledge.items() if k!='answer'})
            result['knowledge_question_ids']=knowledge_qids
            result['knowledge_notes']=knowledge_notes
            result['knowledge_sources']=list({s['id']:s for note in knowledge_notes for s in note['knowledge_sources']}.values())
            result['limitations']=[knowledge['knowledge_scope'],*result.get('limitations',[])]
        return result

    def prepare_analysis(self,owner,body):
        message=body['message'];conversation=body.get('_conversation_id') or self.store.default_conversation(owner)
        turns,reference=self.context(owner,conversation)
        devices=device_ids(message)
        # A correction such as "不是 inv_07，是 inv_08" selects the latter.
        if len(devices)>1 and re.search(r'不是|改为|而是',message):devices=devices[-1:]
        if not devices and reference and re.search(r'刚才|该设备|这台设备|重新|再分析',message):
            devices=reference.get('target_device_ids') or list(dict.fromkeys(e['inverter'] for e in reference['events']))
            correction=next((r['user_feedback'] for r in reversed(turns) if r.get('user_feedback',{}).get('device_ids')),None)
            if correction:devices=correction['device_ids'][-1:]
        event_id=body.get('event_id')
        if devices and event_id and self.snapshot.event(event_id)['inverter'] not in devices:event_id=None
        device=(devices[0] if len(devices)==1 else None) if devices else body.get('device_id')
        analysis_text='；'.join(q for q,kind in self.routes(owner,body) if kind=='maintenance') or message
        analysis_text=re.sub(r'\binv[_ -]?(\d{1,3})\b',lambda m:'inv_'+str(int(m[1])).zfill(2),analysis_text,flags=re.I)
        analysis_text=re.sub('demo-2107-','DEMO-2107-',analysis_text,flags=re.I)
        result=self.snapshot.analyze(analysis_text,event_id,device)
        result['facts']=fact_rows(result['events'])
        result['reasoning_policy']={'causal_ranking':'unsupported','missingness':'not_observed','hypotheses':'unranked possibilities, not confirmed facts'}
        result['field_semantics']={'mean_reference_kw':'模型参考功率均值；不是同群实测均值','min_peer_count':'有效其他同群数量，不含本机','peer_mean_kw':'未提供，不得推算','missing_records':'未提供不等于未发生'}
        result['evidence_scope']=devices
        result['target_device_ids']=devices or list(dict.fromkeys(e['inverter'] for e in result['events']))
        result['presentation']='report' if re.search(r'完整|详细|全面|诊断报告|分析报告|^(?:请)?(?:帮我)?分析',message) else 'question_answer'
        if body.get('response_mode') in {'report','chat'}:
            result['presentation']='report' if body['response_mode']=='report' else 'question_answer'
        result['questions']=[{'question_id':str(i),'question':q} for i,q in enumerate(questions(message),1)]
        result['conversation_context']=[context_excerpt(r) for r in turns[-8:]]
        return result

    def finish_analysis(self,owner,body,result,model=None):
        result['facts']=fact_rows(result['events'])
        answers=[];records=[]
        model_answers={a['question_id']:a for a in result.get('model_analysis',{}).get('answers',[])}
        for index,(question,kind) in enumerate(self.routes(owner,body),1):
            qid=str(index)
            if kind!='maintenance':
                direct=self.direct(owner,{**body,'message':question},model,reference_override=result)
                entry=(direct.get('answers') or [{'question':question,'intent':kind,'status':'answered','answer':direct['summary'],'evidence_ids':[]}])[0]
                records.extend(direct.get('history_records',[]))
            else:
                events=result['events'];text=normalized(question);status='answered'
                if re.search(r'通信|缺失|原因|排序|第一|优先',text):
                    answer='现有功率和同群证据无法确定故障原因，也没有依据排列原因优先级；通信日志和缺失采样明细尚未提供。'
                elif re.search(r'多少|实测|参考|同群|功率|差异|异常证据',text):
                    answer='；'.join(f['text'] for f in fact_rows(events)[:3]) or '当前范围未找到登记事件；不能据此判断设备正常。'
                elif re.search(r'核查|排查|检查|核对|下一步|建议',text):
                    answer='；'.join(result['checks'][:3])
                elif qid in model_answers:
                    answer=model_answers[qid]['answer']
                elif len(result['questions'])==1:
                    answer=result.get('model_analysis',{}).get('summary') or result['summary']
                else:
                    answer='当前证据尚不能完整回答此子问题，请补充具体观测或查询对象。';status='insufficient'
                entry={'question':question,'intent':kind,'status':status,'answer':answer,'evidence_ids':result['evidence_ids']}
            entry={**entry,'question_id':qid};answers.append(entry)
        result['answers']=answers
        result['coverage']={'requested':len(answers),'answered':len(answers),'unanswered':[],'insufficient':[a['question_id'] for a in answers if a.get('status')!='answered']}
        if records:result['history_records']=records
        result['hypotheses']=dedupe(result['hypotheses'])
        result['checks']=dedupe(result['checks'])
        if result.get('presentation')=='question_answer':
            result['summary']=answers[0]['answer'] if len(answers)==1 else '已逐项回答；证据不足的项目已单独说明。'
        elif len(answers)>1:
            result['summary']=f'已按 {len(answers)} 个子问题逐项整理；无法确定的事项保持未知。'
        result.pop('conversation_context',None)  # Prior private turns are not echoed into a new result.
        return result
