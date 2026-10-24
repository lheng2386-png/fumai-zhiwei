import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path
from agent.core import Snapshot
from agent.server import Application, Handler
from agent.store import Store


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        cls.base='http://127.0.0.1:'+str(cls.server.server_port)
        snapshot=Snapshot.load(Path(__file__).resolve().parents[1]/'demo-snapshot.json','demo')
        cls.server.app=Application(snapshot,Store(Path(cls.temp.name)/'audit.sqlite'),cls.base)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join()
        assert Path(cls.temp.name).resolve().is_relative_to(Path(__file__).resolve().parent)
        cls.temp.cleanup()

    def call(self,path,body=None,content_type='application/json',origin=None):
        headers={'Origin':origin or self.base,'X-Fumai-Session':str(uuid.uuid4())}
        if body is not None:headers['Content-Type']=content_type
        request=urllib.request.Request(self.base+path,data=body,headers=headers)
        try:
            with urllib.request.urlopen(request,timeout=5) as response:
                return response.status,json.load(response)
        except urllib.error.HTTPError as error:
            return error.code,json.load(error)

    def test_valid_analysis(self):
        status,result=self.call('/agent-api/analyze',json.dumps({'message':'分析 inv_07'}).encode())
        self.assertEqual(status,200);self.assertTrue(result['run_id'])
        self.assertTrue(all(e['inverter']=='inv_07' for e in result['events']))

    def test_http_boundaries(self):
        self.assertEqual(self.call('/agent-api/analyze',b'{}','text/plain')[0],415)
        self.assertEqual(self.call('/agent-api/analyze',b'[')[0],400)
        self.assertEqual(self.call('/agent-api/analyze',b'x'*17000)[0],413)
        self.assertEqual(self.call('/agent-api/analyze',b'{}',origin='https://evil.example')[0],403)
        self.assertEqual(self.call('/agent-api/not-exist')[0],404)
        status,result=self.call('/agent-api/health')
        self.assertEqual(status,200);self.assertFalse(result['device_control'])
        self.assertNotIn('metadata',result)


if __name__=='__main__':unittest.main()
