"""Independent risk-based security/command qualification; isolated databases only.

Oracles are requirement-level: HTTP denial + unchanged persisted objects,
state transition decision table, and explicit submitted/approved/material invariants.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
import json
import pytest
from fastapi.testclient import TestClient
from backend.app import db
from backend.app.main import app

STAMP=datetime(2026,11,24,8,tzinfo=timezone.utc)
@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_PATH',str(tmp_path/'security.sqlite3'))
    monkeypatch.setenv('DATA_ORIGIN','test')
    monkeypatch.setenv('APP_MODE','demo')
    monkeypatch.setenv('BIND_HOST','127.0.0.1')
    monkeypatch.setenv('LLM_MODE','offline')
    monkeypatch.setattr(db,'_clock',lambda:STAMP)
    with TestClient(app,raise_server_exceptions=False) as c:yield c

def login(c,identity='emp01'):
    r=c.post('/api/demo/login',json={'user_id':identity});assert r.status_code==200,r.text
    c.headers['X-CSRF-Token']=r.json()['csrf_token']
    return r

def draft(c,cat='GENERAL_ONBOARDING',fields=None):
    payload={'description':'请核对入职报到流程'}
    if cat=='MATERIAL_EXCEPTION':payload.update(missing_item='入职信息表',expected_date='2026-12-20')
    if cat=='ACCESS_DEVICE':payload['device_type']='账号'
    if fields is not None:payload=fields
    r=c.post('/api/cases/draft',json={'category':cat,'fields':payload});assert r.status_code==200,r.text
    return r.json()

def submit(c,d,key='security-request-001',**changes):
    body={'draft_id':d['id'],'draft_version':d['draft_version'],'confirmation_token':d['confirmation_token'],'confirmed':True,'allow_duplicate':True}
    body.update(changes)
    return c.post('/api/cases',json=body,headers={'Idempotency-Key':key})

def create(c,cat='GENERAL_ONBOARDING'):
    login(c);r=submit(c,draft(c,cat));assert r.status_code==200,r.text;return r.json()

def act(c,case,action,**kwargs):
    return c.post(f"/api/cases/{case['id']}/actions/{action}",json={'row_version':case['row_version'],**kwargs})

def persisted(case_id):
    with db.connect() as c:
        return {table:[dict(r) for r in c.execute('SELECT * FROM '+table+' WHERE '+('id' if table=='cases' else 'case_id')+'=?',(case_id,))] for table in ('cases','tasks','events','messages','materials')}

@pytest.mark.parametrize('actor',['emp02','hr02','it01'])
def test_cross_object_read_write_denials_leave_no_side_effects(client,actor):
    c=client;ca=create(c,'MATERIAL_EXCEPTION');login(c,actor);before=persisted(ca['id'])
    paths=[('GET',f"/api/cases/{ca['id']}",None),('GET',f"/api/cases/{ca['id']}/reply-draft",None)]
    for action in ['accept','ask_employee','create_task','resolve','confirm','reopen','cancel']:
        paths.append(('POST',f"/api/cases/{ca['id']}/actions/{action}",{'row_version':1,'body':'ADVERSARIAL_PAYLOAD','reason':'测试越权','resolution_code':'rejected'}))
    paths.extend([('POST',f"/api/cases/{ca['id']}/messages",{'row_version':1,'body':'ADVERSARIAL_PAYLOAD'}),('POST',f"/api/cases/{ca['id']}/materials",{'row_version':1,'required_item_code':'onboarding_form','provided':True}),('POST','/api/feedback',{'case_id':ca['id'],'ease':5,'outcome':'resolved'})])
    for method,path,body in paths:
        r=c.request(method,path,**({'json':body} if body is not None else {}))
        assert r.status_code in (403,404),(actor,method,path,r.status_code,r.text)
        assert ca['description'] not in r.text
    assert persisted(ca['id'])==before

@pytest.mark.parametrize('actor',['emp01','it01'])
def test_staff_only_apis_deny_employee_and_it(client,actor):
    c=client;login(c,actor)
    for path in ['/api/admin/knowledge','/api/analytics/summary','/api/analytics/details','/api/analytics/export']:
        assert c.get(path).status_code==403,path
    assert c.post('/api/employees/emp01/profile',json={'planned_start_date':'2027-01-02','expected_planned_start_date':'2026-12-01','reason':'无权更改'}).status_code==403

@pytest.mark.parametrize('actor',['emp01','hr01','admin01'])
def test_only_assigned_it_can_complete_tasks(client,actor):
    c=client;ca=create(c,'ACCESS_DEVICE');login(c,'hr01')
    ca=act(c,ca,'accept').json();ca=act(c,ca,'create_task',assignee_id='it02').json();t=ca['tasks'][0]
    login(c,actor);before=persisted(ca['id'])
    r=c.post(f"/api/tasks/{t['id']}/actions/complete",json={'row_version':t['row_version']})
    assert r.status_code==404
    assert persisted(ca['id'])==before

# Independent decision table from task book section 10. Six states × six commands.
ALLOWED={
    'assigned':{'accept':'in_progress','cancel':'cancelled'},
    'in_progress':{'ask_employee':'waiting_employee','resolve':'resolved','cancel':'cancelled'},
    'waiting_employee':{'cancel':'cancelled'},
    'resolved':{'confirm':'closed','reopen':'in_progress','cancel':'cancelled'},
    'closed':{'reopen':'in_progress'},
    'cancelled':{},
}
@pytest.mark.parametrize('state',list(ALLOWED))
@pytest.mark.parametrize('command',['accept','ask_employee','resolve','confirm','reopen','cancel'])
def test_requirement_state_transition_matrix(client,state,command):
    c=client;ca=create(c)
    # Isolated direct fixture selects a precondition; command itself always uses API.
    with db.tx() as con:con.execute('UPDATE cases SET status=? WHERE id=?',(state,ca['id']))
    login(c,'emp01' if command in ('confirm','reopen','cancel') else 'hr01')
    ca=c.get('/api/cases/'+ca['id']).json();before=persisted(ca['id'])
    r=act(c,ca,command,body='依据本请求所附入职制度，请按地点与时间安排报到。',reason='独立状态机测试')
    expected=ALLOWED[state].get(command)
    if expected:
        assert r.status_code==200,(state,command,r.text)
        assert r.json()['status']==expected
        assert r.json()['row_version']==ca['row_version']+1
        transitions=[e for e in r.json()['events'] if e['kind']=='status_changed']
        assert transitions[-1]['metadata']['to_status']==expected
    else:
        assert r.status_code==400,(state,command,r.status_code,r.text)
        assert persisted(ca['id'])==before

@pytest.mark.parametrize('bad_token',['中文令牌','é','\u200b'])
def test_unicode_csrf_token_is_clean_denial_not_server_error(client,bad_token):
    c=client;ca=create(c);before=persisted(ca['id'])
    # HTTP headers are bytes on wire; latin1 decoding permits non-ASCII values.
    r=c.post(f"/api/cases/{ca['id']}/actions/cancel",json={'row_version':1,'reason':'无效令牌'},headers={b'X-CSRF-Token':bad_token.encode('utf-8')})
    assert r.status_code==403,r.text
    assert persisted(ca['id'])==before

@pytest.mark.parametrize('bad_token',['中文令牌','é','\u200b'])
def test_unicode_confirmation_token_is_clean_denial_not_server_error(client,bad_token):
    c=client;login(c);d=draft(c)
    r=submit(c,d,confirmation_token=bad_token)
    assert r.status_code==400,r.text
    with db.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM submission_attempts WHERE draft_id=?',(d['id'],)).fetchone()[0]==0

@pytest.mark.parametrize('elapsed',[timedelta(hours=12),timedelta(hours=12,seconds=1)])
def test_session_expiry_boundary_is_exclusive(client,monkeypatch,elapsed):
    login(client);monkeypatch.setattr(db,'_clock',lambda:STAMP+elapsed)
    assert client.get('/api/me').status_code==401

def test_logout_invalidates_saved_cookie_and_csrf(client):
    c=client;r=login(c);cookie=c.cookies.get('session');token=r.json()['csrf_token']
    assert 'HttpOnly' in r.headers['set-cookie'] and 'SameSite=strict' in r.headers['set-cookie']
    assert c.post('/api/logout').status_code==200
    c.cookies.set('session',cookie);c.headers['X-CSRF-Token']=token
    assert c.get('/api/me').status_code==401
    assert c.post('/api/cases/draft',json={'category':'GENERAL_ONBOARDING','fields':{'description':'cookie重放'}}).status_code==401

@pytest.mark.parametrize('field,value',[('role','admin'),('requester_id','emp02'),('status','resolved'),('policy_snapshot_refs',[{'article_version_id':'fake'}])])
def test_outer_mass_assignment_cannot_override_identity_or_status(client,field,value):
    login(client)
    payload={'category':'GENERAL_ONBOARDING','fields':{'description':'test'},field:value}
    assert client.post('/api/cases/draft',json=payload).status_code==422

@pytest.mark.parametrize('value',[{},[],1,True])
def test_malformed_business_field_persists_failed_confirmation_without_500(client,value):
    c=client;login(c);d=draft(c,'MATERIAL_EXCEPTION',{'description':'检查字段类型','missing_item':value,'expected_date':'2026-12-20'})
    r=submit(c,d);assert r.status_code==422,r.text
    assert 'missing_item' in r.json()['detail']['missing_fields']
    with db.connect() as con:
        row=con.execute('SELECT validation_result,result_case_id FROM submission_attempts WHERE draft_id=?',(d['id'],)).fetchone()
        assert tuple(row)==('invalid',None)

def test_self_reported_shenzhen_case_routes_to_shenzhen_hr(client):
    c=client;login(c,'emp07')
    r=c.post('/api/assistant/context',json={'city':'深圳','planned_start_date':'2026-12-01'});assert r.status_code==200
    ca=submit(c,draft(c,'ACCESS_DEVICE')).json()
    assert ca['context_source']['city']=='深圳'
    assert ca['assigned_group']=='深圳HR' and ca['assigned_to']=='hr02'
    login(c,'hr02');assert c.get('/api/cases/'+ca['id']).status_code==200
    assert any(row['id']==ca['id'] for row in c.get('/api/cases?city=深圳').json())
    ca=act(c,ca,'accept').json();ca=act(c,ca,'create_task',assignee_id='it01').json()
    assert ca['tasks'][0]['minimal_context']['city']=='深圳'
    assert ca['tasks'][0]['minimal_context']['planned_start_date']=='2026-12-01'
    login(c,'hr01');assert c.get('/api/cases/'+ca['id']).status_code==404


def test_concurrent_invalid_retry_is_one_attempt_then_fresh_confirm_is_second(client):
    c=client;login(c);d=draft(c,'MATERIAL_EXCEPTION',{'description':'缺日期','missing_item':'入职信息表'})
    cookies=dict(c.cookies);token=c.headers['X-CSRF-Token'];barrier=Barrier(8)
    def worker(_):
        with TestClient(app,raise_server_exceptions=False) as other:
            other.cookies.update(cookies);other.headers['X-CSRF-Token']=token;barrier.wait()
            response=submit(other,d,'race-invalid-key')
            return response.status_code,response.json()
    with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(worker,range(8)))
    assert {status for status,_ in results}=={422}
    assert len({body['detail']['attempt_id'] for _,body in results})==1
    with db.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM submission_attempts WHERE draft_id=?',(d['id'],)).fetchone()[0]==1
    fields=d['fields']|{'expected_date':'2026-12-20'}
    updated=c.post('/api/cases/draft',json={'draft_id':d['id'],'draft_version':d['draft_version'],'category':d['category'],'fields':fields}).json()
    assert submit(c,updated,'race-invalid-key').status_code==409
    ca=submit(c,updated,'race-valid-new-key');assert ca.status_code==200
    with db.connect() as con:
        rows=con.execute('SELECT attempt_no,validation_result FROM submission_attempts WHERE draft_id=? ORDER BY attempt_no',(d['id'],)).fetchall()
        assert [tuple(r) for r in rows]==[(1,'invalid'),(2,'valid')]


def test_approval_then_reopen_rejection_preserves_real_material_truth(client):
    c=client;ca=create(c,'MATERIAL_EXCEPTION');login(c,'hr01')
    ca=act(c,ca,'accept').json();ca=act(c,ca,'resolve',resolution_code='approved_deferred',body='批准延期，材料仍待实际提交',expected_date='2026-12-20').json()
    assert not ca['materials'][0]['provided'] and ca['tasks'][0]['status']=='pending'
    login(c);ca=act(c,ca,'confirm').json();ca=act(c,ca,'reopen',reason='安排需调整').json()
    login(c,'hr01');ca=act(c,ca,'resolve',resolution_code='rejected',body='本次重开后不再批准延期安排',reason='约定条件发生变化').json()
    assert ca['resolution_code']=='rejected' and not ca['materials'][0]['provided']
    login(c);ca=act(c,ca,'cancel',reason='不再办理').json()
    assert ca['tasks'][0]['status']=='cancelled'
    assert c.post(f"/api/cases/{ca['id']}/materials",json={'row_version':ca['row_version'],'required_item_code':'onboarding_form','provided':True}).status_code==400

@pytest.mark.parametrize('change_source',['verified_profile','self_reported'])
def test_preview_context_change_requires_new_confirmation(client,change_source):
    c=client
    if change_source=='self_reported':
        login(c,'emp07');assert c.post('/api/assistant/context',json={'city':'广州','planned_start_date':'2026-12-01'}).status_code==200
        d=draft(c)
        assert c.post('/api/assistant/context',json={'city':'深圳'}).status_code==200
    else:
        login(c);d=draft(c);login(c,'hr01')
        assert c.post('/api/employees/emp01/profile',json={'planned_start_date':'2027-01-02','expected_planned_start_date':'2026-12-01','reason':'计划变更'}).status_code==200
        login(c)
    r=submit(c,d)
    assert r.status_code==409,('preview changed silently',r.text)
    assert r.json()['detail']['code']=='context_changed'
    with db.connect() as con:
        assert con.execute('SELECT case_id FROM drafts WHERE id=?',(d['id'],)).fetchone()['case_id'] is None
    refreshed=c.post('/api/cases/draft',json={'draft_id':d['id'],'draft_version':d['draft_version'],'category':d['category'],'fields':d['fields']}).json()
    r=submit(c,refreshed,'context-fresh-key');assert r.status_code==200,r.text
    assert r.json()['context_source']==refreshed['context_source']


def test_token_confirmation_requires_boolean_true_not_coercion(client):
    login(client);d=draft(client)
    for value in ['true','yes',1]:
        r=submit(client,d,confirmed=value)
        assert r.status_code==422,(value,r.status_code,r.text)
    with db.connect() as con:
        assert con.execute('SELECT case_id FROM drafts WHERE id=?',(d['id'],)).fetchone()['case_id'] is None


def test_business_date_validation_uses_shanghai_calendar_day(client,monkeypatch):
    # 2026-11-25 00:30 in Shanghai; yesterday must not be accepted as future supply.
    monkeypatch.setattr(db,'_clock',lambda:datetime(2026,11,24,16,30,tzinfo=timezone.utc))
    login(client)
    d=draft(client,'MATERIAL_EXCEPTION',{'description':'已经是上海次日','missing_item':'入职信息表','expected_date':'2026-11-24'})
    r=submit(client,d);assert r.status_code==422,r.text
    assert 'expected_date_in_past' in r.json()['detail']['missing_fields']


def test_escaped_unpaired_surrogate_confirmation_token_does_not_crash(client):
    login(client);d=draft(client)
    body={'draft_id':d['id'],'draft_version':d['draft_version'],'confirmation_token':'\ud800','confirmed':True,'allow_duplicate':True}
    r=client.post('/api/cases',content=json.dumps(body),headers={'Content-Type':'application/json','Idempotency-Key':'surrogate-key'})
    assert r.status_code==400,r.text


def test_unauthenticated_read_write_and_export_fail_closed(client):
    for path in ['/api/me','/api/cases','/api/cases/demo_case_001','/api/tasks','/api/knowledge/pol-report-gz-v2','/api/assistant/history','/api/analytics/export','/api/admin/knowledge']:
        assert client.get(path).status_code==401,path
    for path,body in [('/api/cases/draft',{'category':'GENERAL_ONBOARDING','fields':{'description':'未登录'}}),('/api/assistant/query',{'question':'入职清单'}),('/api/feedback',{'case_id':'demo_case_001','ease':5,'outcome':'resolved'})]:
        assert client.post(path,json=body).status_code==401,path
