import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from agent.core import Snapshot
from agent.model import ChatCompletionsModel, ResponsesModel
from agent.server import Application
from agent.store import Store


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.snapshot=Snapshot.load(Path(__file__).resolve().parents[1]/'demo-snapshot.json','demo')
        self.store=Store(Path(self.temp.name)/'audit.sqlite')
        self.app=Application(self.snapshot,self.store,'http://localhost')
        self.headers={'Origin':'http://localhost','X-Fumai-Client':str(uuid.uuid4()),'X-Fumai-Session':str(uuid.uuid4())}

    def tearDown(self):self.temp.cleanup()

    def ask(self,message,**extra):
        return self.app.dispatch('POST','/agent-api/analyze',self.headers,{'message':message,**extra},'test')

    def attach_model(self,text='交流侧要看并网点电压，直流侧要看光伏输入与启动条件；时间相关性不能直接证明原因。',responses=False):
        payloads=[]
        def transport(payload):
            payloads.append(payload)
            bundle=json.loads(payload['input'][0]['content'] if responses else payload['messages'][1]['content'])['evidence_bundle']
            value={'summary':text,'hypotheses':[],'checks':[],'unknowns':[],'evidence_ids':[],
                   'answers':[{'question_id':q['question_id'],'answer':text,'evidence_ids':[]} for q in bundle['questions']]}
            encoded=json.dumps(value,ensure_ascii=False)
            if responses:return {'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':encoded}]}]}
            return {'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':encoded}}]}
        cls=ResponsesModel if responses else ChatCompletionsModel
        self.app.model=cls('test-key','test-model','https://model.example/v1',transport)
        return payloads

    def test_undervoltage_answers_without_device_analysis_or_tools(self):
        with patch.object(self.snapshot,'analyze',side_effect=AssertionError('No diagnosis')),patch.object(self.snapshot,'tool',side_effect=AssertionError('No tool')):
            result=self.ask('光伏逆变器出现欠压，一般是什么原因？')
        self.assertEqual(result['intent'],'knowledge')
        self.assertIn('交流',result['summary']);self.assertIn('直流',result['summary']);self.assertIn('母线',result['summary'])
        self.assertEqual(result['events'],[]);self.assertEqual(result['trace'],[])
        self.assertEqual(len(result['knowledge_sources']),2)

    def test_knowledge_model_has_distinct_instructions_and_no_device_evidence(self):
        payloads=self.attach_model()
        with patch.object(self.snapshot,'analyze',side_effect=AssertionError('No diagnosis')),patch.object(self.snapshot,'tool',side_effect=AssertionError('No tool')):
            result=self.ask('光伏逆变器出现欠压，一般是什么原因？')
        self.assertEqual(result['model_status'],'connected')
        self.assertNotIn('tools',payloads[0])
        self.assertIn('光伏知识助手',payloads[0]['messages'][0]['content'])
        bundle=json.loads(payloads[0]['messages'][1]['content'])['evidence_bundle']
        self.assertEqual(bundle['reply_mode'],'knowledge');self.assertEqual(bundle['events'],[])
        self.assertTrue(bundle['knowledge_notes'])

    def test_knowledge_supports_three_turns_with_real_previous_answer(self):
        payloads=self.attach_model()
        first=self.ask('光伏逆变器出现欠压，一般是什么原因？')
        second=self.ask('那如果只在清晨出现呢？')
        third=self.ask('你这样解释的依据是什么？')
        self.assertEqual(second['intent'],'followup');self.assertEqual(third['intent'],'followup')
        self.assertEqual(third['knowledge_topic'],'undervoltage')
        bundle=json.loads(payloads[-1]['messages'][1]['content'])['evidence_bundle']
        self.assertTrue(any(r['question']=='那如果只在清晨出现呢？' and r['summary']==second['summary'] for r in bundle['conversation_context']))
        self.assertTrue(all(r['summary']!=first['summary'] or r['question']!=third['question'] for r in bundle['conversation_context']))
        self.assertEqual(third['events'],[])

    def test_basis_variants_reference_original_analysis_without_new_diagnosis(self):
        first=self.ask('分析 inv_07 的异常证据')
        variants=['判断依据是什么？','你做出这个判断的依据是什么？','为什么这么判断？','有哪些数据支持这个判断？','能展开解释一下吗？','解释一下']
        with patch.object(self.snapshot,'analyze',side_effect=AssertionError('No repeat diagnosis')):
            for question in variants:
                result=self.ask(question)
                self.assertEqual(result['intent'],'followup',question)
                self.assertEqual(result['source_run_ids'],[first['run_id']],question)
                self.assertIn('9.4',result['summary']);self.assertIn('27.8',result['summary'])
                self.assertNotIn('不会触发',result['summary'])

    def test_latest_knowledge_topic_wins_over_an_older_device_analysis(self):
        first=self.ask('分析 inv_07 的异常证据')
        self.ask('光伏逆变器出现欠压，一般是什么原因？')
        reply=self.ask('那如果只在清晨出现呢？')
        self.assertEqual(reply['knowledge_topic'],'undervoltage');self.assertIn('清晨',reply['summary'])
        self.assertEqual(reply['events'],[]);self.assertNotIn('9.4',reply['summary'])
        device=self.ask('刚才 inv_07 判断依据是什么？')
        self.assertEqual(device['source_run_ids'],[first['run_id']]);self.assertIn('9.4',device['summary'])

    def test_new_conversation_does_not_inherit_knowledge_topic(self):
        self.ask('光伏逆变器出现欠压，一般是什么原因？')
        self.headers['X-Fumai-Session']=str(uuid.uuid4())
        reply=self.ask('判断依据是什么？')
        self.assertNotIn('knowledge_topic',reply)
        self.assertEqual(reply['answers'][0]['status'],'insufficient')

    def test_model_failure_keeps_useful_curated_knowledge(self):
        self.app.model=ChatCompletionsModel('key','test','https://model.example',lambda _:(_ for _ in ()).throw(TimeoutError()))
        reply=self.ask('光伏逆变器出现欠压，一般是什么原因？')
        self.assertEqual(reply['model_status'],'failed_fallback')
        self.assertIn('交流',reply['summary']);self.assertIn('直流',reply['summary'])
        self.assertEqual(reply['events'],[])

    def test_invented_voltage_threshold_falls_back_without_publishing_it(self):
        self.attach_model('任何逆变器直流电压小于 123.4 V 都是欠压，交流侧不用看。')
        reply=self.ask('逆变器欠压一般是什么原因？')
        self.assertEqual(reply['model_status'],'failed_fallback');self.assertNotIn('123.4',reply['summary'])

    def test_general_pv_question_outside_curated_topics_can_use_model(self):
        self.attach_model('热斑是组件局部温度异常升高的现象，遮挡或失配是待核对的通用方向；不能据此确认某块组件损坏。')
        reply=self.ask('光伏组件为什么会出现热斑？')
        self.assertEqual(reply['intent'],'knowledge');self.assertEqual(reply['model_status'],'connected')
        self.assertIn('热斑',reply['summary']);self.assertEqual(reply['knowledge_sources'],[])

    def test_compound_questions_keep_business_and_device_answers_separate(self):
        self.attach_model()
        reply=self.ask('逆变器欠压一般是什么原因？；实际发电量是多少？')
        self.assertEqual(len(reply['answers']),2)
        self.assertEqual(reply['answers'][1]['status'],'not_connected')
        self.assertEqual(reply['knowledge_question_ids'],['1'])

    def test_responses_protocol_also_uses_the_knowledge_lane(self):
        payloads=self.attach_model(responses=True)
        reply=self.ask('光伏逆变器出现欠压，一般是什么原因？')
        self.assertEqual(reply['model_status'],'connected');self.assertEqual(payloads[0]['tools'],[])
        self.assertIn('光伏知识助手',payloads[0]['instructions'])

    def test_selected_event_is_not_used_as_evidence_of_a_general_voltage_question(self):
        reply=self.ask('逆变器欠压一般是什么原因？',event_id='DEMO-2107-001')
        self.assertEqual(reply['intent'],'knowledge');self.assertEqual(reply['events'],[])

    def test_voltage_word_in_a_basis_followup_does_not_bypass_device_evidence(self):
        first=self.ask('分析 inv_07 的异常证据')
        reply=self.ask('这台逆变器欠压的判断依据是什么？')
        self.assertEqual(reply['intent'],'followup');self.assertNotIn('knowledge_topic',reply)
        self.assertEqual(reply['source_run_ids'],[first['run_id']])
        self.assertIn('不能',reply['summary'])


if __name__=='__main__':unittest.main()
