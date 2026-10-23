import base64
import copy
import io
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer

from agent.core import Snapshot, InputError
from agent.store import Store
from agent.knowledge import Knowledge, extract
from agent.mcp import MCPClient, VERSION
from agent.model import ChatCompletionsModel
from agent.server import Application, Handler, HTTPError


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'audit.sqlite');self.kb=Knowledge(self.store)
        self.owner='demo:'+str(uuid.uuid4())

    def upload(self,text='逆变器故障码 E073 表示绝缘电阻低。核对设备型号和告警时间，由有资质人员按现场规程进一步核查。',owner=None,name='运维手册.txt'):
        return self.kb.upload(owner or self.owner,{'filename':name,'content_base64':base64.b64encode(text.encode()).decode()})

    def wait(self,doc):
        for _ in range(200):
            result=self.kb.get(self.owner,doc['id'])
            if result['status']!='processing':return result
            time.sleep(.01)
        self.fail('ingestion did not finish')

    def test_chinese_bm25_citation_and_isolation(self):
        doc=self.wait(self.upload());self.assertEqual(doc['status'],'ready')
        result=self.kb.search(self.owner,'E073 绝缘电阻低要核查什么？')
        self.assertTrue(result['chunks']);chunk=result['chunks'][0]
        self.assertEqual(chunk['filename'],'运维手册.txt');self.assertIn('绝缘电阻低',chunk['text'])
        self.assertTrue(chunk['citation_id'].startswith('KB:'));self.assertEqual(chunk['digest'],__import__('hashlib').sha256(chunk['text'].encode()).hexdigest())
        self.assertEqual(self.kb.search('other','E073')['chunks'],[])
        with self.assertRaises(InputError):self.kb.chunk('other',chunk['chunk_id'])
        with self.assertRaises(InputError):self.kb.delete('other',doc['id'])
        again=self.upload();self.assertEqual(again['id'],doc['id'])
        self.assertEqual(len(self.kb.list(self.owner)),1)
        self.kb.delete(self.owner,doc['id']);self.assertEqual(self.kb.search(self.owner,'E073')['chunks'],[])
        with self.assertRaises(InputError):self.kb.chunk(self.owner,chunk['chunk_id'])

    def test_failed_document_explains_how_to_fix_and_retry(self):
        doc=self.wait(self.upload('short'))
        self.assertEqual(doc['status'],'failed');self.assertIn('OCR',doc['error'])
        doc=self.wait(self.kb.retry(self.owner,doc['id']));self.assertEqual(doc['status'],'failed')
        self.kb.delete(self.owner,doc['id'])

    def test_docx_extracts_only_text_and_rejects_corrupt_files(self):
        file=io.BytesIO()
        with zipfile.ZipFile(file,'w') as archive:
            archive.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>故障码 E073：绝缘电阻异常，核对设备型号、告警时段与原始记录。</w:t></w:r></w:p></w:body></w:document>')
        pages=extract('manual.docx',file.getvalue());self.assertEqual(pages[0][0],'段落 1');self.assertIn('E073',pages[0][1])
        with self.assertRaises(InputError):extract('bad.docx',b'not a docx')
        with self.assertRaises(InputError):extract('bad.pdf',b'not a PDF')
        with patch('agent.knowledge.shutil.which',return_value=None),self.assertRaisesRegex(InputError,'组件'):
            extract('manual.pdf',b'%PDF-1.7')

    def test_upload_limits_formats_and_no_silent_match(self):
        for body in ({'filename':'../manual.txt','content_base64':'YWJj'},{'filename':'x.exe','content_base64':'YWJj'}, {'filename':'a.txt','content_base64':'not base64!'}):
            with self.assertRaises(InputError):self.kb.upload(self.owner,body)
        self.wait(self.upload())
        result=self.kb.search(self.owner,'ZX999999');self.assertEqual(result['chunks'],[]);self.assertIn('未找到',result['notice'])


class MCPHTTPTests(KnowledgeTests):
    def setUp(self):
        super().setUp()
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.addCleanup(self.server.server_close)
        self.origin='http://127.0.0.1:'+str(self.server.server_port)
        snapshot=Snapshot.load(Path(__file__).resolve().parents[1]/'demo-snapshot.json','demo')
        # Reuse one Knowledge instance; no task may be active when initializing app.
        self.app=Application(snapshot,self.store,self.origin,mcp_endpoint=self.origin+'/agent-api/mcp')
        self.kb=self.app.knowledge
        self.server.app=self.app
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.server.shutdown)
        self.session=self.owner.removeprefix('demo:')
        self.headers={'X-Fumai-Session':self.session,'Origin':self.origin}

    def request(self,path,body=None,headers=None):
        h={**self.headers,**(headers or {})}
        if body is not None:h['Content-Type']='application/json'
        request=urllib.request.Request(self.origin+'/agent-api/'+path,data=None if body is None else json.dumps(body).encode(),headers=h)
        return urllib.request.urlopen(request,timeout=10)

    def test_real_http_mcp_handshake_discovery_and_tools(self):
        self.wait(self.upload())
        client=MCPClient(self.origin+'/agent-api/mcp',self.headers)
        self.assertEqual(len(client.tools),5)
        answer=client.tool('search_knowledge',{'query':'E073','limit':5});self.assertTrue(answer['chunks'])
        detail=client.tool('get_knowledge_chunk',{'chunk_id':answer['chunks'][0]['chunk_id']});self.assertIn('E073',detail['text'])
        with self.assertRaises(InputError):client.tool('execute_command',{'command':'x'})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.request('mcp',{'jsonrpc':'2.0','id':1,'method':'tools/list'},{'Origin':'https://evil.example','Accept':'application/json, text/event-stream'})
        self.assertEqual(cm.exception.code,403)
        with self.request('mcp',{'jsonrpc':'2.0','method':'notifications/initialized'},{'Accept':'application/json, text/event-stream'}) as response:
            self.assertEqual(response.status,202);self.assertEqual(response.read(),b'')
        with self.assertRaises(urllib.error.HTTPError) as cm:self.request('mcp')
        self.assertEqual(cm.exception.code,405)

    def test_rag_calls_real_mcp_and_model_cites_actual_document(self):
        self.wait(self.upload())
        requests=[]
        def model(payload):
            requests.append(copy.deepcopy(payload))
            evidence=json.loads(payload['messages'][1]['content'])['evidence_bundle']
            citation=evidence['knowledge_sources'][0]['citation_id']
            return {'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps({'summary':'资料中 E073 表示绝缘电阻低，需核对设备型号与告警时间。','hypotheses':[],'checks':['核对设备型号和告警时间。'],'unknowns':['仍需现场核对。'],'evidence_ids':[citation]},ensure_ascii=False)}}]}
        self.app.model=ChatCompletionsModel('test-key','qwen-test','https://model.example/v1/chat/completions',model)
        result=self.app.analyze(self.owner,{'message':'E073 是什么？','scope':'knowledge'})
        self.assertEqual(result['mcp_status'],'connected');self.assertEqual(result['model_status'],'connected')
        self.assertEqual(result['events'],[]);self.assertEqual(result['knowledge_sources'][0]['title'],'运维手册.txt')
        self.assertIn('KB:',result['model_analysis']['evidence_ids'][0]);self.assertTrue(any(t.get('transport')=='mcp' for t in result['trace']))
        self.assertEqual(self.store.get_run(self.owner,result['run_id'])['knowledge_sources'],result['knowledge_sources'])
        # Deleting an indexed source does not silently rewrite historical answers.
        self.kb.delete(self.owner,result['knowledge_sources'][0]['document_id'])
        self.assertTrue(self.store.get_run(self.owner,result['run_id'])['knowledge_sources'])

    def test_fabricated_document_citation_falls_back_preserving_sources(self):
        self.wait(self.upload())
        self.app.model=ChatCompletionsModel('test-key','qwen-test','https://model.example/v1/chat/completions',lambda payload:{'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps({'summary':'invented','hypotheses':[],'checks':[],'unknowns':[],'evidence_ids':['KB:fake']})}}]})
        result=self.app.analyze(self.owner,{'message':'E073 是什么','scope':'knowledge'})
        self.assertEqual(result['model_status'],'failed_fallback');self.assertTrue(result['knowledge_sources']);self.assertNotIn('model_analysis',result)

    def test_live_viewer_cannot_upload_or_delete(self):
        snapshot_doc=json.loads((Path(__file__).resolve().parents[1]/'demo-snapshot.json').read_text());snapshot_doc['source']='measured'
        app=Application(Snapshot(snapshot_doc,'live'),self.store,'https://plant.example',users={'a'*32:{'id':'reader','role':'viewer'}})
        for path in ('knowledge/documents','knowledge/documents/id/delete','knowledge/documents/id/retry'):
            with self.assertRaises(HTTPError) as cm:app.dispatch('POST','/agent-api/'+path,{'Authorization':'Bearer '+'a'*32,'X-Forwarded-Proto':'https','Origin':'https://plant.example'},{},'client')
            self.assertEqual(cm.exception.status,403)

    def test_upload_http_status_and_document_sse(self):
        with self.request('knowledge/documents',{'filename':'manual.txt','content_base64':base64.b64encode(('故障码 E073，绝缘电阻低。请核对设备型号和原始告警时间。').encode()).decode()}) as response:
            doc=json.load(response)
        doc=self.wait(doc)
        with self.request('knowledge/documents/'+doc['id']+'/events') as response:
            self.assertIn('text/event-stream',response.headers['Content-Type']);text=response.read().decode();self.assertIn('event: done',text)
        with self.request('knowledge/search',{'query':'E073','limit':5}) as response:self.assertTrue(json.load(response)['chunks'])


if __name__=='__main__':unittest.main()
