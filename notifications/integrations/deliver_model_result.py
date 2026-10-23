#!/usr/bin/env python3
"""Deliver an inference report without importing GPU dependencies or exposing credentials."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def deliver_report(report, endpoint, token, attempts=3):
    parsed=urllib.parse.urlparse(endpoint)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path!='/api/model-ingestions':
        raise ValueError('接收地址必须指向 /api/model-ingestions，不能在地址中放入凭证或参数')
    if parsed.scheme!='https' and not(parsed.scheme=='http' and parsed.hostname in {'127.0.0.1','localhost','::1'}):
        raise ValueError('请使用 HTTPS 或本机/SSH 转发地址，不能通过公网明文发送接入凭证')
    if not token or len(token)<32 or '\n' in token or '\r' in token:
        raise ValueError('请在私密环境变量 FUMAI_MODEL_INGEST_TOKEN 中配置至少 32 字符的接入凭证')
    if not isinstance(report,dict):raise ValueError('结果文件必须是 JSON 对象')
    outgoing=dict(report)
    # CLI inference reports may lack the HTTP facade's request_id. Stable ID survives restarts.
    if not outgoing.get('request_id'):
        original=json.dumps(report,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
        outgoing['request_id']='report-'+hashlib.sha256(original).hexdigest()
    payload=json.dumps(outgoing,ensure_ascii=False,separators=(',',':')).encode()
    if len(payload)>65536:raise ValueError('结果超过 64 KiB，请按 evidence 分批并为每批指定 request_id')
    opener=urllib.request.build_opener(NoRedirect())
    for attempt in range(attempts):
        req=urllib.request.Request(endpoint,data=payload,headers={'Content-Type':'application/json','Authorization':'Bearer '+token},method='POST')
        try:
            with opener.open(req,timeout=10) as response:
                body=response.read(512*1024)
                return json.loads(body)
        except urllib.error.HTTPError as error:
            if error.code<500:
                try:detail=json.loads(error.read(4096)).get('error','请求未接收')
                except (ValueError,AttributeError):detail='请求未接收，请检查接口地址与接入配置'
                raise ValueError(f'接收接口返回 {error.code}：{detail}') from None
        except (urllib.error.URLError,TimeoutError,OSError):pass
        if attempt+1<attempts:time.sleep(2**attempt)
    raise RuntimeError('业务服务暂时不可用。原结果文件未修改；稍后重试将使用相同请求编号，不会重复记日志')


def main():
    parser=argparse.ArgumentParser(description='将三模型识别报告写入伏脉业务系统；不运行模型')
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--receipt',type=Path,required=True,help='新的私密接收回执文件；不会覆盖已有文件')
    parser.add_argument('--endpoint',default='http://127.0.0.1:8787/api/model-ingestions')
    args=parser.parse_args()
    if args.receipt.exists():raise ValueError('回执文件已存在，请先读取原回执，或指定一个新文件名')
    if args.input.stat().st_size>65536:raise ValueError('结果文件超过 64 KiB，请按 evidence 分批')
    report=json.loads(args.input.read_text(encoding='utf-8'))
    result=deliver_report(report,args.endpoint,os.environ.get('FUMAI_MODEL_INGEST_TOKEN',''))
    # A unique temp file and atomic rename avoid leaving a truncated receipt after process interruption.
    args.receipt.parent.mkdir(parents=True,exist_ok=True)
    temporary=args.receipt.with_name(args.receipt.name+'.'+uuid.uuid4().hex+'.tmp')
    descriptor=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(descriptor,'w') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    try:os.link(temporary,args.receipt)
    finally:temporary.unlink(missing_ok=True)
    print(f"已接收 {result['accepted']} 条，重复 {result['duplicates']} 条，需要修改 {result['rejected']} 条；详细原因已写入回执文件")
    return 2 if result['rejected'] else 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except (ValueError,RuntimeError,OSError) as error:
        print(str(error));raise SystemExit(1)
