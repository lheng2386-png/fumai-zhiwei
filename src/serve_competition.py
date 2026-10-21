"""Local HTTP facade for pinned competition inference and evidence review.

One worker, serialized inference, no user-selected checkpoint/path or training.
Uploads use generated job directories. Bind localhost by default.
"""
import argparse
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
from threading import Lock
from typing import Optional, Literal
import uuid
from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ConfigDict
from starlette.concurrency import run_in_threadpool
from competition_infer import run as infer, verified_file, sha256
from competition_evidence import assess, utc_time
from competition_feedback import job_result, save as save_feedback, history as feedback_history

os.environ['YOLO_AUTOINSTALL']='false'

TASKS=('timeseries','classification','detection')
MAX_UPLOAD=16*1024*1024


class ReviewRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    evidence: list[dict]=Field(max_length=1000)
    plant_id: str=Field(min_length=1,max_length=128)
    asset_id: str=Field(min_length=1,max_length=128)
    as_of: str
    max_age_minutes: float=Field(default=30,gt=0,le=1440)
    max_pair_gap_minutes: float=Field(default=15,ge=0,le=1440)



class FeedbackRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id: str=Field(pattern=r'^[a-f0-9]{32}$')
    status: Literal['confirmed_fault','expected_operation','data_issue','needs_investigation']
    reviewer: str=Field(min_length=1,max_length=128)
    note: str=Field(min_length=1,max_length=4000)


def create_app(root=None,bundle=None,output=None,device='cpu',origins=None,runner=None):
    root=Path(root or Path(__file__).resolve().parents[1]).resolve()
    bundle=Path(bundle or root/'configs/competition_models_v1.json').resolve()
    output=Path(output or root/'reports/competition_api_jobs').resolve()
    execute=runner or infer
    lock=Lock()
    def readiness():
        config=json.loads(bundle.read_text())
        state={}
        for task in TASKS:
            spec=config['models'][task]
            verified_file(root,spec['checkpoint'])
            if 'normalization' in spec:verified_file(root,spec['normalization'])
            state[task]={'architecture':spec['architecture'],
                         'model_sha256':spec['checkpoint']['sha256'],
                         'threshold_version':spec['threshold_version']}
        return config,state

    @asynccontextmanager
    async def lifespan(app):
        # Verify required weights before serving; no first-request downloads.
        readiness();output.mkdir(parents=True,exist_ok=True)
        yield

    app=FastAPI(title='伏脉智维：比赛模型接口',version='1.0.0',lifespan=lifespan,
                description='三模型真实推理与可解释证据复核；本地比赛服务。')
    if origins:
        app.add_middleware(CORSMiddleware,allow_origins=list(origins),allow_methods=['GET','POST'],
                           allow_headers=['Content-Type'],allow_credentials=False)

    @app.middleware('http')
    async def content_size(request,call_next):
        length=request.headers.get('content-length')
        if length:
            try:too_large=int(length)>MAX_UPLOAD+1024*1024
            except ValueError:return JSONResponse(status_code=400,content={'detail':'Invalid Content-Length'})
            if too_large:return JSONResponse(status_code=413,content={'detail':'Request exceeds local demo upload limit'})
        return await call_next(request)

    @app.get('/health')
    def health():
        try:config,state=readiness()
        except (ValueError,OSError,KeyError):
            raise HTTPException(status_code=503,detail='Pinned model files unavailable or changed')
        return {'status':'ready','bundle_version':config['version'],'bundle_sha256':sha256(bundle),
                'device':device,'models':state,'inference_policy':'serialized, one request at a time',
                'model_weights_verified':True,'models_warmed_up':False}

    @app.post('/v1/infer/{task}')
    async def predict(task:Literal['timeseries','classification','detection'],
                      file:UploadFile=File(...),plant_id:str=Form(...),asset_id:str=Form(...),
                      observation_id:str=Form(...),
                      data_kind:Literal['public_research','site_observation','synthetic_demo']=Form(...),
                      observed_at:Optional[str]=Form(None)):
        if any(not v.strip() or len(v)>128 for v in (plant_id,asset_id,observation_id)):
            raise HTTPException(status_code=422,detail='Nonempty plant/asset/observation IDs required, up to 128 characters')
        if task!='timeseries':
            try:utc_time(observed_at)
            except ValueError as error:raise HTTPException(status_code=422,detail=str(error))
        payload=await file.read(MAX_UPLOAD+1)
        await file.close()
        if not payload:raise HTTPException(status_code=422,detail='Empty upload')
        if len(payload)>MAX_UPLOAD:raise HTTPException(status_code=413,detail='File exceeds 16 MiB')
        # Do not permit user filenames to become filesystem paths.
        job_id=uuid.uuid4().hex;directory=output/job_id;directory.mkdir(parents=True)
        source=directory/('input.csv' if task=='timeseries' else 'input.image')
        source.write_bytes(payload)
        args=argparse.Namespace(task=task,bundle=bundle,root=root,input=source,
            output=directory/'result.json',plant_id=plant_id,asset_id=asset_id,
            observation_id=observation_id,data_kind=data_kind,observed_at=observed_at,device=device)
        def compute():
            with lock:
                try:readiness()
                except (ValueError,OSError,KeyError):
                    raise HTTPException(status_code=503,detail='Pinned model files unavailable or changed')
                return execute(args)
        try:
            result=await run_in_threadpool(compute)
        except HTTPException:raise
        except (ValueError,KeyError,OSError) as error:
            (directory/'error.json').write_text(json.dumps({'type':type(error).__name__,'detail':str(error)})+'\n')
            raise HTTPException(status_code=422,detail={'code':'invalid_model_input','message':str(error),'request_id':job_id})
        except Exception as error:
            (directory/'error.json').write_text(json.dumps({'type':type(error).__name__,'detail':str(error)})+'\n')
            raise HTTPException(status_code=500,detail={'code':'model_runtime_failed','request_id':job_id})
        return {'request_id':job_id,**result}

    @app.post('/v1/review')
    def review(body:ReviewRequest):
        try:result=assess(body.evidence,body.plant_id,body.asset_id,body.as_of,
                          body.max_age_minutes,body.max_pair_gap_minutes)
        except (ValueError,TypeError) as error:raise HTTPException(status_code=422,detail=str(error))
        job_id=uuid.uuid4().hex;directory=output/job_id;directory.mkdir(parents=True)
        (directory/'review.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        return {'request_id':job_id,**result}

    @app.get('/v1/jobs/{request_id}')
    def get_job(request_id:str):
        try:
            result,digest=job_result(output,request_id)
            return {'request_id':request_id,'report_sha256':digest,'result':result,
                    'feedback_history':feedback_history(output,request_id)}
        except FileNotFoundError as error:raise HTTPException(status_code=404,detail=str(error))
        except ValueError as error:raise HTTPException(status_code=422,detail=str(error))

    @app.post('/v1/feedback',status_code=201)
    def human_feedback(body:FeedbackRequest):
        try:return save_feedback(output,body.request_id,body.status,body.reviewer,body.note)
        except FileNotFoundError as error:raise HTTPException(status_code=404,detail=str(error))
        except ValueError as error:raise HTTPException(status_code=422,detail=str(error))


    return app


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    p.add_argument('--bundle',type=Path);p.add_argument('--output',type=Path)
    p.add_argument('--device',choices=('cpu','cuda'),default='cpu')
    p.add_argument('--host',default='127.0.0.1');p.add_argument('--port',type=int,default=8770)
    p.add_argument('--allow-origin',action='append',default=[])
    args=p.parse_args()
    import uvicorn
    uvicorn.run(create_app(args.root,args.bundle,args.output,args.device,args.allow_origin),
                host=args.host,port=args.port,workers=1)
