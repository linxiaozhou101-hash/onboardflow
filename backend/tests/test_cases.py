import os
import pytest
from fastapi.testclient import TestClient
from backend.app import db
from backend.app.main import app

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_PATH',str(tmp_path/'cases.sqlite3'))
    with TestClient(app) as c:yield c

def login(c,id='emp01'):
    r=c.post('/api/demo/login',json={'user_id':id});assert r.status_code==200,r.text
    c.headers['X-CSRF-Token']=r.json()['csrf_token'];return r.json()['user']
def draft(c,category='MATERIAL_EXCEPTION',fields=None,**extra):
    r=c.post('/api/cases/draft',json={'category':category,'fields':fields or {'description':'测试缺件','missing_item':'入职信息表','expected_date':'2027-01-01'},**extra});assert r.status_code==200,r.text;return r.json()
def submit(c,d,key='test-key-00001',**extra):
    return c.post('/api/cases',headers={'Idempotency-Key':key},json={'draft_id':d['id'],'draft_version':d['draft_version'],'confirmation_token':d['confirmation_token'],'confirmed':True,'allow_duplicate':True,**extra})
def command(c,ca,action,**extra):
    return c.post(f"/api/cases/{ca['id']}/actions/{action}",json={'row_version':ca['row_version'],**extra})
def create(c,cat='MATERIAL_EXCEPTION',fields=None):
    login(c);r=submit(c,draft(c,cat,fields));assert r.status_code==200,r.text;return r.json()
def accept(c,ca):
    login(c,'hr01');r=command(c,ca,'accept');assert r.status_code==200,r.text;return r.json()

def test_draft_confirmation_idempotence_and_payload_conflict(client):
    c=client;login(c);before=len(c.get('/api/cases').json());d=draft(c)
    assert len(c.get('/api/cases').json())==before
    assert submit(c,d,confirmed=False).status_code==400
    r=submit(c,d);assert r.status_code==200,r.text
    assert submit(c,d).json()['id']==r.json()['id']
    assert submit(c,d,allow_duplicate=False).status_code==409
    assert len(c.get('/api/cases').json())==before+1

def test_blocked_first_attempt_persists_retry_does_not_duplicate(client):
    c=client;login(c);d=draft(c,fields={'description':'缺资料'})
    assert submit(c,d).status_code==422
    assert submit(c,d).status_code==422
    with db.tx() as con:
        rows=con.execute('SELECT * FROM submission_attempts WHERE draft_id=?',(d['id'],)).fetchall();assert len(rows)==1;assert rows[0]['validation_result']=='invalid';assert rows[0]['result_case_id'] is None
    updated=draft(c,draft_id=d['id'],draft_version=d['draft_version']);assert submit(c,updated,key='test-key-new').status_code==200
    with db.tx() as con:
        attempts=con.execute('SELECT attempt_no,validation_result FROM submission_attempts WHERE draft_id=? ORDER BY attempt_no',(d['id'],)).fetchall()
        assert [tuple(x) for x in attempts]==[(1,'invalid'),(2,'valid')]

def test_deferred_approval_is_not_material_completion(client):
    c=client;ca=accept(c,create(c));r=command(c,ca,'resolve',body='批准本次模拟延期，材料仍需补交。',resolution_code='approved_deferred',expected_date='2027-01-02');assert r.status_code==200,r.text
    ca=r.json();assert ca['status']=='resolved';assert ca['materials'][0]['provided']==0
    assert ca['tasks'][0]['required']==0 and ca['tasks'][0]['status']=='pending'
    login(c);ca=command(c,ca,'confirm').json();assert ca['status']=='closed'
    r=c.post(f"/api/cases/{ca['id']}/materials",json={'row_version':ca['row_version'],'required_item_code':'入职信息表','provided':True});assert r.status_code==200,r.text
    ca=r.json();assert ca['materials'][0]['provided']==1 and ca['tasks'][0]['status']=='completed'
    assert ca['status']=='closed' and ca['resolution_code']=='approved_deferred'

def test_access_it_flow_with_required_dependency_and_confirmation(client):
    c=client;ca=accept(c,create(c,'ACCESS_DEVICE',{'description':'电脑账号未准备','device_type':'电脑和账号'}))
    assert command(c,ca,'resolve',body='完成').status_code==400
    ca=command(c,ca,'create_task',assignee_id='it01',task_type='IT_ACCESS').json();first=ca['tasks'][0]
    ca=command(c,ca,'create_task',assignee_id='it01',task_type='IT_DEVICE',dependency_task_ids=[first['id']]).json();second=next(t for t in ca['tasks'] if t['id']!=first['id'])
    assert command(c,ca,'resolve',body='完成').json()['detail']['code']=='pending_tasks'
    login(c,'it01');assert c.get(f"/api/cases/{ca['id']}").status_code==404
    assert c.post(f"/api/tasks/{second['id']}/actions/complete",json={'row_version':1}).status_code==400
    for t in (first,second):assert c.post(f"/api/tasks/{t['id']}/actions/complete",json={'row_version':1}).status_code==200
    login(c,'hr01');ca=c.get(f"/api/cases/{ca['id']}").json();ca=command(c,ca,'resolve',body='演示任务已完成，请确认').json()
    assert ca['status']=='resolved';login(c);assert command(c,ca,'confirm').json()['status']=='closed'

def test_internal_note_never_leaks_or_counts_response(client):
    c=client;ca=accept(c,create(c));r=c.post(f"/api/cases/{ca['id']}/messages",json={'row_version':ca['row_version'],'body':'ONLY_HR_SECRET','visibility':'internal'});assert r.status_code==200
    ca=r.json();assert ca['first_human_response_at'] is None
    login(c);r=c.get(f"/api/cases/{ca['id']}");assert 'ONLY_HR_SECRET' not in r.text
    assert c.post(f"/api/cases/{ca['id']}/messages",json={'row_version':ca['row_version'],'body':'secret','visibility':'internal'}).status_code==403
    login(c,'hr01');ca=command(c,ca,'ask_employee',body='请补充时间').json();assert ca['first_human_response_at'] and ca['status']=='waiting_employee'
    login(c);r=c.post(f"/api/cases/{ca['id']}/messages",json={'row_version':ca['row_version'],'body':'明天补交'});assert r.json()['status']=='in_progress'

def test_cross_user_group_and_it_authorization(client):
    c=client;ca=create(c);login(c,'emp02')
    assert c.get(f"/api/cases/{ca['id']}").status_code==404
    assert all(x['requester_id']=='emp02' for x in c.get('/api/cases?requester_id=emp01').json())
    assert command(c,ca,'cancel',reason='越权').status_code==404
    login(c,'hr02');assert c.get(f"/api/cases/{ca['id']}").status_code==404
    login(c,'it01');assert c.get('/api/cases').status_code==403;assert c.get('/api/analytics/summary').status_code==403
    assert all(x['assignee_id']=='it01' and 'description' not in x['minimal_context'] for x in c.get('/api/tasks').json())

def test_cancel_atomic_task_lifecycle_and_stale_update(client):
    c=client;ca=accept(c,create(c,'ACCESS_DEVICE',{'description':'账号','device_type':'账号'}));old=ca
    ca=command(c,ca,'create_task',assignee_id='it01').json();task=ca['tasks'][0]
    assert command(c,old,'ask_employee',body='过时').status_code==409
    ca=command(c,ca,'cancel',reason='取消演示').json();assert ca['status']=='cancelled' and ca['tasks'][0]['status']=='cancelled'
    assert command(c,ca,'create_task',assignee_id='it01').status_code==400
    assert command(c,ca,'reopen',reason='复活').status_code==400
    login(c,'it01');assert c.post(f"/api/tasks/{task['id']}/actions/complete",json={'row_version':2}).status_code==400

def test_reopen_preserves_sla_and_completed_tasks(client):
    c=client;ca=accept(c,create(c,'ACCESS_DEVICE',{'description':'账号','device_type':'账号'}));ca=command(c,ca,'create_task',assignee_id='it01').json();t=ca['tasks'][0]
    login(c,'it01');c.post(f"/api/tasks/{t['id']}/actions/complete",json={'row_version':1})
    login(c,'hr01');ca=c.get(f"/api/cases/{ca['id']}").json();ca=command(c,ca,'resolve',body='演示任务已完成').json();due=ca['due_at'];resolved=ca['latest_resolved_at']
    login(c);ca=command(c,ca,'confirm').json();ca=command(c,ca,'reopen',reason='需要进一步核对').json()
    assert ca['status']=='in_progress' and ca['reopen_count']==1 and ca['due_at']==due and ca['latest_resolved_at']==resolved
    assert ca['tasks'][0]['status']=='completed'

def test_transaction_rollback_no_orphan_task(client):
    c=client;ca=accept(c,create(c));before=len(ca['tasks'])
    assert command(c,ca,'resolve',body='',resolution_code='approved_deferred',expected_date='2027-01-02').status_code==400
    current=c.get(f"/api/cases/{ca['id']}").json();assert len(current['tasks'])==before and current['row_version']==ca['row_version'] and current['status']=='in_progress'

def test_csrf_schema_and_persistence(client):
    c=client;ca=create(c);del c.headers['X-CSRF-Token']
    assert command(c,ca,'cancel',reason='bad').status_code==403
    assert c.post('/api/demo/login',json={'user_id':'emp01','role':'admin'}).status_code==422
    assert c.post('/api/demo/login',json={'user_id':'emp01'},headers={'Origin':'https://evil.example'}).status_code==403
    with TestClient(app) as second:
        login(second);assert second.get(f"/api/cases/{ca['id']}").json()['description']==ca['description']
