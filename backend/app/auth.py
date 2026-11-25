import os, secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from fastapi import APIRouter, Request, Response, Depends
from pydantic import BaseModel, ConfigDict
from .db import tx,unpack,now,error,log
from .schemas import Account,SessionState,SimpleResult
router=APIRouter()
class Login(BaseModel):
    model_config=ConfigDict(extra='forbid')
    user_id: str

def demo_allowed(request:Request|None=None):
    if os.getenv('APP_MODE','demo')!='demo' or os.getenv('BIND_HOST','127.0.0.1') not in ('127.0.0.1','localhost','::1'):
        error('demo_disabled','演示登录仅在本机演示模式开放',403)
    if request is not None and request.client and request.client.host not in ('127.0.0.1','::1','localhost','testclient'):
        error('demo_disabled','演示身份切换只接受本机连接',403)

def user(request:Request):
    token=request.cookies.get('session')
    with tx() as c:
        s=unpack(c.execute('SELECT * FROM sessions WHERE id=?',(token,)).fetchone())
        if not s or datetime.fromisoformat(s['created_at']) + timedelta(hours=12) <= datetime.fromisoformat(now()):
            error('unauthenticated','请先选择演示身份登录',401)
        if request.method not in ('GET','HEAD','OPTIONS'):
            token=request.headers.get('X-CSRF-Token','')
            if not token.isascii() or not secrets.compare_digest(token,s['csrf']):
                error('csrf_failed','会话已变化，请刷新后重试',403)
        return unpack(c.execute('SELECT * FROM users WHERE id=?',(s['user_id'],)).fetchone())

def require_hr(u):
    if u['role'] not in ('hr','admin'): error('forbidden','此操作需要 HR 权限',403)

def can_case(u,case):
    if u['role']=='employee': return case['requester_id']==u['id']
    if u['role']=='admin': return True
    return u['role']=='hr' and case['assigned_group']==u['group_name']

def get_case(c,id,u):
    case=unpack(c.execute('SELECT * FROM cases WHERE id=?',(id,)).fetchone())
    if not case or not can_case(u,case): error('not_found','请求不存在或无权访问',404)
    owner=c.execute('SELECT company FROM users WHERE id=?',(case['requester_id'],)).fetchone()
    if owner['company']!=u['company']: error('not_found','请求不存在或无权访问',404)
    return case

@router.get('/api/demo/accounts', response_model=list[Account])
def accounts(request:Request):
    demo_allowed(request)
    with tx() as c: return [unpack(r) for r in c.execute('SELECT * FROM users ORDER BY role,id')]

@router.post('/api/demo/login', response_model=SessionState)
def login(body:Login,response:Response,request:Request):
    demo_allowed(request)
    with tx() as c:
        u=unpack(c.execute('SELECT * FROM users WHERE id=?',(body.user_id,)).fetchone())
        if not u: error('invalid_account','演示账号不存在',400)
        token,csrf=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
        c.execute('INSERT INTO sessions VALUES (?,?,?,?)',(token,u['id'],csrf,now()))
    response.set_cookie('session',token,httponly=True,samesite='strict',secure=os.getenv('COOKIE_SECURE','false')=='true',max_age=43200)
    return {'user':u,'csrf_token':csrf}

@router.get('/api/me', response_model=SessionState)
def me(request:Request,u=Depends(user)):
    with tx() as c: csrf=c.execute('SELECT csrf FROM sessions WHERE id=?',(request.cookies.get('session'),)).fetchone()['csrf']
    return {'user':u,'csrf_token':csrf}

@router.post('/api/logout', response_model=SimpleResult)
def logout(request:Request,response:Response,u=Depends(user)):
    with tx() as c: c.execute('DELETE FROM sessions WHERE id=?',(request.cookies.get('session'),))
    response.delete_cookie('session');return {'ok':True}
