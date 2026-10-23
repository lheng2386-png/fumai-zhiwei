import copy
import json
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent.core import InputError, Snapshot
from agent.conversation import guard_model
from agent.model import ChatCompletionsModel
from agent.server import Application, HTTPError
from agent.store import Store


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.path=Path(self.temp.name)/'audit.sqlite'
        self.store=Store(self.path)
        self.snapshot=Snapshot.load(Path(__file__).resolve().parents[1]/'demo-snapshot.json','demo')
        self.app=Application(self.snapshot,self.store,'http://localhost')
        self.client=str(uuid.uuid4());self.session=str(uuid.uuid4())
        self.headers={'Origin':'http://localhost','X-Fumai-Client':self.client,'X-Fumai-Session':self.session}

    def tearDown(self):self.temp.cleanup()

    def call(self,message,headers=None,**extra):
        return self.app.dispatch('POST','/agent-api/analyze',headers or self.headers,{'message':message,**extra},'test')

    def analysis(self):return self.call('分析 inv_07 的异常证据')

    def test_history_queries_never_reanalyze_or_call_a_model(self):
        first=self.analysis()
        self.app.model=SimpleNamespace(model='test',enrich=Mock(side_effect=AssertionError('No model')))
        with patch.object(self.snapshot,'analyze',side_effect=AssertionError('No new diagnosis')):
            for question in ['列出之前的设备分析记录','刚才是否分析过该设备','查询 inv_07 的历史分析记录']:
                answer=self.call(question)
                self.assertEqual(answer['intent'],'history')
                self.assertEqual([r['run_id'] for r in answer['history_records']],[first['run_id']])
                self.assertEqual(answer['model_status'],'not_invoked')
        self.app.model.enrich.assert_not_called()

    def test_history_is_owner_wide_but_just_now_is_conversation_local(self):
        first=self.analysis()
        other={**self.headers,'X-Fumai-Session':str(uuid.uuid4())}
        self.assertEqual(self.call('刚才是否分析过 inv_07',other)['history_records'],[])
        self.assertEqual(self.call('列出之前的设备分析记录',other)['history_records'][0]['run_id'],first['run_id'])
        self.assertEqual(self.call('为什么这样判断？',other)['answers'][0]['status'],'insufficient')

    def test_repeat_analysis_in_a_new_conversation_is_allowed(self):
        first=self.analysis();other={**self.headers,'X-Fumai-Session':str(uuid.uuid4())}
        second=self.call('分析 inv_07 的异常证据',other)
        self.assertNotEqual(first['run_id'],second['run_id'])
        self.assertNotEqual(first['conversation_id'],second['conversation_id'])

    def test_another_guest_cannot_read_runs_or_conversations(self):
        first=self.analysis();other={**self.headers,'X-Fumai-Client':str(uuid.uuid4()),'X-Fumai-Session':str(uuid.uuid4())}
        self.assertEqual(self.call('列出之前的设备分析记录',other)['history_records'],[])
        for endpoint,body in [('run',{'run_id':first['run_id']}),('conversations',{'conversation_id':first['conversation_id']})]:
            with self.assertRaises(InputError):self.app.dispatch('POST','/agent-api/'+endpoint,other,body,'test')

    def test_followup_uses_stored_facts_without_new_snapshot_analysis(self):
        first=self.analysis()
        with patch.object(self.snapshot,'analyze',side_effect=AssertionError('Must reuse prior evidence')):
            answer=self.call('为什么这样判断？')
        self.assertEqual(answer['intent'],'followup')
        self.assertEqual(answer['source_run_ids'],[first['run_id']])
        self.assertIn('9.4',answer['summary'])
        self.assertNotIn('model_analysis',answer)

    def test_followup_model_has_context_and_no_device_tools(self):
        self.analysis();payloads=[]
        model=ChatCompletionsModel('test-key','test-qwen','https://model.example/v1/chat/completions',lambda p: payloads.append(p) or {'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps({'summary':'运行背景核对能区分观测差异和运行安排，尚不能确认原因。','hypotheses':[],'checks':[],'unknowns':[],'evidence_ids':['DEMO-2107-001'],'answers':[{'question_id':'1','answer':'核对限发记录可以验证运行安排，而检修记录用于核对维护背景；目前两者都未知。','evidence_ids':['DEMO-2107-001']}]},ensure_ascii=False)}}]})
        self.app.model=model
        with patch.object(self.snapshot,'analyze',side_effect=AssertionError('No repeat diagnosis')):
            answer=self.call('这些观测如何支持刚才的判断？')
        self.assertEqual(answer['model_status'],'connected')
        self.assertIn('检修记录',answer['summary'])  # Deterministic transport text is preserved, not replaced by a report template.
        self.assertNotIn('tools',payloads[0])
        bundle=json.loads(payloads[0]['messages'][1]['content'])['evidence_bundle']
        self.assertTrue(bundle['conversation_context'])
        self.assertFalse(bundle['allow_tools'])

    def test_correction_does_not_overwrite_facts_or_confirm_a_cause(self):
        self.analysis();answer=self.call('不对，通信异常没有证据，不应该排第一')
        self.assertFalse(answer['user_feedback']['verified'])
        self.assertEqual(self.snapshot.event('DEMO-2107-001')['mean_actual_kw'],9.4)
        reply=self.call('通信异常为什么排在首位？')
        self.assertIn('不能确认通信故障',reply['summary'])
        self.assertIn('纠正',reply['summary'])

    def test_corrected_device_is_not_replaced_with_previous_device_evidence(self):
        self.analysis();self.call('不是 inv_07，而是 inv_08')
        reply=self.call('重新分析该设备')
        self.assertEqual(reply['events'],[])
        self.assertNotIn('inv_07',reply['summary'])

    def test_multiple_questions_each_have_a_result_and_missing_business_data_is_explicit(self):
        self.analysis()
        result=self.call('列出之前的设备分析记录；什么是同群证据？；实际发电量是多少？；短信和邮件发送成功了吗？')
        self.assertEqual(result['coverage']['requested'],4)
        self.assertEqual(len(result['answers']),4)
        self.assertEqual([a['intent'] for a in result['answers']],['history','knowledge','business','business'])
        self.assertEqual([a['status'] for a in result['answers'][2:]],['not_connected','not_connected'])

    def test_subquestions_during_new_analysis_use_the_current_evidence(self):
        result=self.call('分析 inv_07；实测功率是多少？；属于单机还是共性？')
        self.assertEqual(len(result['answers']),3)
        self.assertIn('9.4',result['answers'][1]['answer'])
        self.assertIn('单机偏离',result['answers'][2]['answer'])

    def test_compound_diagnosis_and_followup_refer_to_the_same_current_evidence(self):
        result=self.call('分析 inv_07；为什么这样判断？')
        self.assertEqual(result['coverage']['requested'],2)
        self.assertIn('9.4',result['answers'][1]['answer'])

    def test_conjunctions_numbered_questions_and_no_event_history_are_not_lost(self):
        result=self.call('1. 查询设备负责人以及短信发送状态；2. 实际发电量是多少？')
        self.assertEqual(len(result['answers']),3)
        self.assertTrue(all(a['status']=='not_connected' for a in result['answers']))
        self.call('分析 inv_08')
        history=self.call('刚才是否分析过 inv_08')
        self.assertEqual(len(history['history_records']),1)
        reply=self.call('为什么这样判断？')
        self.assertEqual(reply['answers'][0]['status'],'insufficient')

    def test_direct_observation_query_is_not_a_new_diagnosis(self):
        with patch.object(self.snapshot,'analyze',side_effect=AssertionError('No diagnosis')):
            answer=self.call('inv_07 实测功率是多少？')
        self.assertEqual(answer['intent'],'evidence_query')
        self.assertIn('9.4',answer['summary'])
        self.assertTrue(answer['facts'])

    def test_history_pronoun_uses_selected_device_without_substituting_another(self):
        self.analysis()
        other=next(e for e in self.snapshot.events.values() if e['inverter']=='inv_19')
        reply=self.call('刚才是否分析过该设备',event_id=other['event_id'])
        self.assertEqual(reply['history_records'],[])

    def test_two_observation_queries_keep_all_citations_and_their_versions(self):
        answer=self.call('inv_07 实测功率是多少？；inv_19 实测功率是多少？')
        cited={i for a in answer['answers'] for i in a['evidence_ids']}
        self.assertEqual(cited,set(answer['evidence_ids']))
        self.assertEqual({e['inverter'] for e in answer['events']},{'inv_07','inv_19'})
        self.assertEqual(len(answer['evidence_sources']),len(cited))

    def test_response_mode_changes_presentation_not_evidence(self):
        report=self.call('分析 inv_07 的异常证据',response_mode='report')
        chat=self.call('分析 inv_07 的异常证据',response_mode='chat')
        self.assertEqual(report['presentation'],'report');self.assertEqual(chat['presentation'],'question_answer')
        self.assertEqual(report['events'],chat['events'])

    def test_context_persists_after_restarting_application(self):
        run=self.analysis();self.app=Application(self.snapshot,Store(self.path),'http://localhost')
        bootstrap=self.app.dispatch('GET','/agent-api/bootstrap',self.headers,{},'test')
        self.assertEqual(bootstrap['turns'][0]['run_id'],run['run_id'])
        self.assertEqual(self.call('为什么这样判断？')['source_run_ids'],[run['run_id']])

    def test_configured_account_has_same_history_across_browser_ids(self):
        token='a'*32;self.app=Application(self.snapshot,self.store,'https://plant.example',{token:{'id':'alice','role':'operator'}})
        h={**self.headers,'Origin':'https://plant.example','X-Forwarded-Proto':'https','Authorization':'Bearer '+token}
        run=self.call('分析 inv_07',h)
        h2={**h,'X-Fumai-Client':str(uuid.uuid4()),'X-Fumai-Session':str(uuid.uuid4())}
        history=self.call('列出之前的设备分析记录',h2)
        self.assertEqual(history['history_records'][0]['run_id'],run['run_id'])
        self.assertEqual(history['conversation_id'],h2['X-Fumai-Session'])
        self.assertEqual(self.app.dispatch('GET','/agent-api/bootstrap',h2,{},'test')['identity']['kind'],'account')

    def test_browser_id_cannot_forge_an_account_or_bypass_transport(self):
        self.app=Application(self.snapshot,self.store,'http://localhost',{'a'*32:{'id':'alice','role':'operator'}})
        with self.assertRaises(HTTPError):self.call('列出分析记录',{**self.headers,'Authorization':'Bearer '+'a'*32})
        with self.assertRaises(HTTPError):self.call('列出分析记录',{**self.headers,'X-Fumai-Client':'alice'})

    def test_legacy_session_history_remains_private_and_accessible_after_upgrade(self):
        old={k:v for k,v in self.headers.items() if k!='X-Fumai-Client'}
        run=self.call('分析 inv_07',old)
        reply=self.call('列出之前的设备分析记录')
        self.assertEqual(reply['history_records'][0]['run_id'],run['run_id'])
        draft=self.app.dispatch('POST','/agent-api/drafts',self.headers,{'run_id':run['run_id'],'event_id':'DEMO-2107-001'},'test')
        self.assertEqual(draft['status'],'pending_confirmation')
        self.assertEqual(self.store.get_run('demo:'+self.session,run['run_id'])['run_id'],run['run_id'])

    def test_additive_schema_remains_compatible_with_old_five_column_inserts(self):
        with self.store.connect() as db:
            self.assertEqual(len(db.execute('PRAGMA table_info(runs)').fetchall()),5)
            db.execute('INSERT INTO runs VALUES(?,?,?,?,?)',(str(uuid.uuid4()),'legacy','2026-10-22','old',json.dumps({'summary':'old','events':[]})))
        reopened=Store(self.path)
        self.assertEqual(len(reopened.history('legacy')),1)

    def test_cause_ranking_missingness_and_invented_numbers_are_rejected(self):
        events=self.analysis()['events']
        for statement in ['最可能的原因是通信故障','无法确认，但最可能是通信故障','通信异常排在第一位','存在数据缺失','限发导致功率偏低','实测功率是 999 kW','同群参考值为 27.8kW','没有通信日志进一步佐证异常']:
            with self.subTest(statement=statement),self.assertRaises(ValueError):
                guard_model({'summary':statement,'hypotheses':[]},events)
        guard_model({'summary':'无法给原因排序；没有通信日志，尚不能确认通信故障。','hypotheses':['可能存在运行变化，需核查。']},events)
        guard_model({'summary':'没有通信日志，不能因此证明某个原因。','hypotheses':[]},events)

    def test_rejected_model_explanation_falls_back_to_observed_facts(self):
        def enrich(snapshot,message,result):
            result['model_analysis']={'summary':'最可能的原因是通信故障','hypotheses':[],'answers':[]}
            return result
        self.app.model=SimpleNamespace(model='fake',enrich=enrich)
        run=self.analysis()
        self.assertEqual(run['model_status'],'failed_fallback')
        self.assertEqual(run['engine'],'rules')
        self.assertNotIn('model_analysis',run)
        self.assertEqual(run['facts'][0]['event_id'],'DEMO-2107-001')


if __name__=='__main__':unittest.main()
