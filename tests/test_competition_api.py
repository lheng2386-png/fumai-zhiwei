import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from serve_competition import create_app
from test_competition_evidence import record

class CompetitionApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.weight=self.root/'weights.pt';self.weight.write_bytes(b'fixture')
        spec={'architecture':'fixture','threshold_version':'fixed',
              'checkpoint':{'path':'weights.pt','sha256':hashlib.sha256(b'fixture').hexdigest()}}
        self.bundle=self.root/'bundle.json';self.bundle.write_text(json.dumps({'version':'test',
             'models':{task:spec for task in ('timeseries','classification','detection')}}))
        self.seen=[]
        def fake(args):
            self.seen.append(args)
            result={'schema_version':'competition-inference-v1','task':args.task,'evidence':[]}
            args.output.write_text(json.dumps(result));return result
        self.client=TestClient(create_app(self.root,self.bundle,self.root/'jobs',runner=fake))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None,None,None);self.temp.cleanup()

    def fields(self):return {'plant_id':'p1','asset_id':'a1','observation_id':'o1',
                            'data_kind':'synthetic_demo','observed_at':'2026-10-21T10:00:00Z'}

    def test_health_checks_hash_and_never_calls_model(self):
        self.assertEqual(self.client.get('/health').status_code,200)
        self.assertEqual(self.seen,[])
        self.weight.write_bytes(b'corrupt')
        self.assertEqual(self.client.get('/health').status_code,503)
        response=self.client.post('/v1/infer/classification',data=self.fields(),files={'file':('x.png',b'payload')})
        self.assertEqual(response.status_code,503);self.assertEqual(self.seen,[])

    def test_upload_is_saved_under_generated_name_not_user_path(self):
        response=self.client.post('/v1/infer/classification',data=self.fields(),
                                  files={'file':('../../escape.png',b'image-data')})
        self.assertEqual(response.status_code,200)
        args=self.seen[0]
        self.assertEqual(args.input.name,'input.image');self.assertEqual(args.input.read_bytes(),b'image-data')
        self.assertTrue((self.root/'jobs').resolve() in args.input.parents)
        self.assertEqual(len(response.json()['request_id']),32)

    def test_invalid_time_empty_upload_and_unknown_task_rejected(self):
        fields={**self.fields(),'observed_at':'no-time'}
        for route,data,file in [('/v1/infer/classification',fields,b'payload'),
                                ('/v1/infer/classification',self.fields(),b''),
                                ('/v1/infer/unknown',self.fields(),b'payload')]:
            r=self.client.post(route,data=data,files={'file':('x',file)})
            self.assertEqual(r.status_code,422)
        self.assertEqual(self.seen,[])

    def test_review_preserves_conflict_and_rejects_future_evidence_support(self):
        fields={'plant_id':'p1','asset_id':'a1','as_of':'2026-10-21T10:10:00Z',
                'evidence':[record('timeseries'),record('classification','normal')]}
        r=self.client.post('/v1/review',json=fields)
        self.assertEqual(r.status_code,200);self.assertEqual(r.json()['status'],'conflict_review')
        fields['evidence']=[record('timeseries',observed_at='2026-10-21T11:00:00Z')]
        self.assertEqual(self.client.post('/v1/review',json=fields).json()['status'],'insufficient_evidence')
        fields['evidence']=[record('timeseries'),record('timeseries')]
        self.assertEqual(self.client.post('/v1/review',json=fields).status_code,422)

    def test_oversized_request_rejected_before_inference(self):
        r=self.client.post('/v1/infer/classification',content=b'x',
                           headers={'Content-Length':str(20*1024*1024),'Content-Type':'application/octet-stream'})
        self.assertEqual(r.status_code,413);self.assertEqual(self.seen,[])

    def test_model_input_error_is_not_a_normal_prediction(self):
        def fail(args):raise ValueError('CSV missing columns')
        with TestClient(create_app(self.root,self.bundle,self.root/'error-jobs',runner=fail)) as client:
            r=client.post('/v1/infer/timeseries',data=self.fields(),files={'file':('input.csv',b'wrong')})
            self.assertEqual(r.status_code,422)
            self.assertEqual(r.json()['detail']['code'],'invalid_model_input')
            self.assertNotIn('evidence',r.json())

    def test_feedback_retrievable_and_cannot_rewrite_original(self):
        r=self.client.post('/v1/infer/classification',data=self.fields(),files={'file':('module.png',b'fixture')})
        identifier=r.json()['request_id']
        initial=self.client.get('/v1/jobs/'+identifier)
        self.assertEqual(initial.status_code,200)
        digest=initial.json()['report_sha256']
        body={'request_id':identifier,'status':'needs_investigation','reviewer':'test-reviewer','note':'Review source evidence'}
        self.assertEqual(self.client.post('/v1/feedback',json=body).status_code,201)
        self.assertEqual(self.client.post('/v1/feedback',json={**body,'status':'data_issue','note':'Metadata requires checking'}).status_code,201)
        saved=self.client.get('/v1/jobs/'+identifier).json()
        self.assertEqual(saved['report_sha256'],digest)
        self.assertEqual(len(saved['feedback_history']),2)
        self.assertTrue(all(r['matches_current_report'] for r in saved['feedback_history']))
        self.assertEqual(self.client.post('/v1/feedback',json={**body,'request_id':'f'*32}).status_code,404)
        self.assertEqual(self.client.post('/v1/feedback',json={**body,'note':' '}).status_code,422)

if __name__=='__main__':unittest.main()
