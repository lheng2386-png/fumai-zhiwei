"""Read-only MCP 2025-11-25 JSON-RPC tools and a loopback Streamable HTTP client."""
import json
import urllib.request
import uuid
from urllib.parse import urlsplit

from .core import InputError
from .model import TOOLS

VERSION='2025-11-25'


class MCPServer:
    def __init__(self,snapshot,knowledge):
        self.snapshot,self.knowledge=snapshot,knowledge

    @staticmethod
    def definitions():
        return [{'name':t['name'],'description':t['description'],'inputSchema':t['parameters'],
                 'annotations':{'readOnlyHint':True,'destructiveHint':False,'openWorldHint':False}} for t in TOOLS]

    def call(self,owner,name,args):
        if not isinstance(args,dict):raise InputError('工具参数必须为对象。')
        if name=='search_knowledge' and set(args)=={'query','limit'}:
            return self.knowledge.search(owner,**args)
        if name=='get_knowledge_chunk' and set(args)=={'chunk_id'}:
            return self.knowledge.chunk(owner,**args)
        return self.snapshot.tool(name,args)

    def handle(self,owner,body):
        if not isinstance(body,dict) or body.get('jsonrpc')!='2.0' or not isinstance(body.get('method'),str):
            return {'jsonrpc':'2.0','id':None,'error':{'code':-32600,'message':'Invalid JSON-RPC request'}}
        request_id=body.get('id')
        if request_id is not None and (isinstance(request_id,bool) or not isinstance(request_id,(str,int))):
            return {'jsonrpc':'2.0','id':None,'error':{'code':-32600,'message':'Invalid request id'}}
        method,params=body['method'],body.get('params',{})
        if 'id' not in body:
            return None  # accepted notifications, no response body
        result=None;error=None
        if not isinstance(params,dict):error={'code':-32602,'message':'Invalid parameters'}
        elif method=='initialize':
            result={'protocolVersion':VERSION,'capabilities':{'tools':{'listChanged':False}},'serverInfo':{'name':'fumai-operations','version':'0.3.0'}}
        elif method=='ping':result={}
        elif method=='tools/list':result={'tools':self.definitions()}
        elif method=='tools/call':
            if params.get('name') not in {t['name'] for t in TOOLS}:
                error={'code':-32602,'message':'Unknown tool'}
            else:
                try:
                    data=self.call(owner,params['name'],params.get('arguments',{}))
                    result={'content':[{'type':'text','text':json.dumps(data,ensure_ascii=False)}],'structuredContent':data,'isError':False}
                except (InputError,TypeError,ValueError,KeyError) as exc:
                    result={'content':[{'type':'text','text':str(exc) if isinstance(exc,InputError) else '工具参数无效。'}],'isError':True}
        else:error={'code':-32601,'message':'Method not found'}
        return {'jsonrpc':'2.0','id':request_id,**({'error':error} if error else {'result':result})}


class MCPClient:
    """No caller-selected URLs: only this service's explicitly configured loopback endpoint."""
    def __init__(self,endpoint,headers,transport=None):
        self.endpoint,self.headers,self.transport=endpoint,headers,transport
        if endpoint:
            url=urlsplit(endpoint)
            if url.scheme!='http' or url.hostname!='127.0.0.1' or url.path!='/agent-api/mcp' or url.query or url.fragment or url.username:
                raise ValueError('MCP endpoint must be the local service endpoint')
        initialized=self.rpc('initialize',{'protocolVersion':VERSION,'capabilities':{},'clientInfo':{'name':'fumai-agent','version':'0.3.0'}})
        if initialized.get('protocolVersion')!=VERSION:raise InputError('MCP 协议版本不兼容。')
        self.rpc('notifications/initialized',{},notification=True)
        self.tools=self.rpc('tools/list',{})['tools']
        if {t['name'] for t in self.tools}!={t['name'] for t in TOOLS}:raise InputError('MCP 工具清单与允许范围不一致。')

    def rpc(self,method,params,notification=False):
        body={'jsonrpc':'2.0','method':method,'params':params}
        if not notification:body['id']=str(uuid.uuid4())
        if self.transport:
            result=self.transport(body)
        else:
            request=urllib.request.Request(self.endpoint,data=json.dumps(body).encode(),headers={**self.headers,
                'Content-Type':'application/json','Accept':'application/json, text/event-stream','MCP-Protocol-Version':VERSION},method='POST')
            with urllib.request.build_opener(NoRedirect()).open(request,timeout=8) as response:
                raw=response.read(256*1024+1)
                if len(raw)>256*1024:raise InputError('MCP 工具返回内容过大。')
                if notification:
                    if response.status!=202:raise InputError('MCP 通知未被接收。')
                    return None
                result=json.loads(raw)
        if notification:return None
        if not result or result.get('id')!=body['id'] or result.get('error'):
            raise InputError('MCP 工具连接或调用失败，请稍后重试。')
        return result['result']

    def tool(self,name,args):
        if name not in {t['name'] for t in TOOLS}:raise InputError('工具不在允许范围内。')
        answer=self.rpc('tools/call',{'name':name,'arguments':args})
        if answer.get('isError'):raise InputError(answer['content'][0]['text'])
        return answer['structuredContent']


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise InputError('MCP 端点不能重定向。')
