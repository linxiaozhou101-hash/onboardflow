"""Fault injection and cross-request isolation, independent of acceptance labels."""
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
import pytest
import httpx
from fastapi.testclient import TestClient
from backend.app import db,providers
from backend.app.main import app

@pytest.fixture
def clients(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_PATH',str(tmp_path/'resilience.sqlite3'))
    monkeypatch.setenv('DATA_ORIGIN','test')
    monkeypatch.setenv('LLM_MODE','remote')
    monkeypatch.setenv('LLM_API_KEY','TEST_ONLY_PLACEHOLDER')
    monkeypatch.setenv('LLM_ENDPOINT','https://unused.invalid/chat/completions')
    monkeypatch.setenv('LLM_MODEL','stub')
    with TestClient(app,raise_server_exceptions=False) as a, TestClient(app,raise_server_exceptions=False) as b:
        for client,identity in [(a,'emp01'),(b,'emp02')]:
            state=client.post('/api/demo/login',json={'user_id':identity}).json()
            client.headers['X-CSRF-Token']=state['csrf_token']
        yield a,b

def test_slow_provider_does_not_hold_database_write_lock(clients,monkeypatch):
    a,b=clients;started=threading.Event();release=threading.Event()
    def blocked_provider(*args,**kwargs):
        started.set();release.wait(3)
        raise httpx.ReadTimeout('injected slow provider')
    monkeypatch.setattr(providers.httpx,'post',blocked_provider)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(a.post,'/api/assistant/query',json={'question':'入职需要准备什么材料？'})
        assert started.wait(2),'provider injection not reached'
        connection=db.connect();connection.execute('PRAGMA busy_timeout=150')
        try:
            try:
                connection.execute('BEGIN IMMEDIATE');locked=False;connection.rollback()
            except sqlite3.OperationalError as exc:
                assert 'locked' in str(exc);locked=True
        finally:
            connection.close();release.set()
        response=future.result(timeout=4)
    assert response.status_code==200,response.text
    assert response.json()['provider_mode']=='offline_fallback'
    assert not locked,'Outbound model wait held SQLite global writer lock, blocking unrelated users'

@pytest.mark.parametrize('payload',[None,{}, {'choices':[]}, {'choices':[{'message':{'content':'{broken'}}]}, {'choices':[{'message':{'content':json.dumps({'action':'approve','answer_text':'已批准','citation_ids':['pol-exception-full']})}}]}, {'choices':[{'message':{'content':json.dumps({'action':'answer','answer_text':'fake','citation_ids':['not-authorized']})}}]}])
def test_provider_malformed_payload_does_not_break_authoritative_answer(clients,monkeypatch,payload):
    captured=[]
    def reply(*args,**kwargs):
        captured.append(kwargs['json'])
        return httpx.Response(200,json=payload,request=httpx.Request('POST','https://unused.invalid'))
    monkeypatch.setattr(providers.httpx,'post',reply)
    a,_=clients
    with db.tx() as c:before=c.execute('SELECT COUNT(*) FROM cases').fetchone()[0]
    r=a.post('/api/assistant/query',json={'question':'入职信息表缺失，可否补交申请？'})
    assert r.status_code==200,r.text
    answer=r.json();assert answer['provider_mode']=='offline_fallback'
    assert answer['action']=='answer' and answer['citation_refs'] and '审批' in answer['answer_text']
    with db.tx() as c:assert c.execute('SELECT COUNT(*) FROM cases').fetchone()[0]==before
    transmitted=json.dumps(captured,ensure_ascii=False)
    assert 'TEST_ONLY_PLACEHOLDER' not in transmitted and 'pol-internal' not in transmitted
    assert 'TEST_ONLY_PLACEHOLDER' not in r.text


def test_same_conversation_cannot_be_claimed_by_two_users_after_slow_call(clients,monkeypatch):
    a,b=clients;first=threading.Event();release=threading.Event()
    def provider(*args,**kwargs):
        first.set();release.wait(2)
        raise httpx.ReadTimeout('injected')
    monkeypatch.setattr(providers.httpx,'post',provider)
    with ThreadPoolExecutor(max_workers=2) as pool:
        fa=pool.submit(a.post,'/api/assistant/query',json={'question':'入职报到地点？','conversation_id':'competing-conversation'})
        assert first.wait(1)
        fb=pool.submit(b.post,'/api/assistant/query',json={'question':'入职报到地点？','conversation_id':'competing-conversation'})
        release.set();responses=[fa.result(timeout=4),fb.result(timeout=4)]
    assert sorted(r.status_code for r in responses)==[200,403]
    with db.tx() as c:
        assert c.execute('SELECT COUNT(DISTINCT user_id) FROM knowledge_answers WHERE conversation_id=?',('competing-conversation',)).fetchone()[0]==1
