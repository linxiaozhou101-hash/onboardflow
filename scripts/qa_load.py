"""Bounded local HTTP benchmark and restart check on a disposable DB.
Never targets the user's running :8000 service or default database.
"""
import concurrent.futures,datetime,json,os,platform,signal,sqlite3,subprocess,sys,tempfile,time,uuid,statistics,hashlib,importlib.metadata
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'reports/qa_20261124';BASE='http://127.0.0.1:8002'

def pct(vals,p):
    vals=sorted(vals);return round(vals[min(len(vals)-1,int((len(vals)-1)*p))],2)

def launch(env,log):
    proc=subprocess.Popen([sys.executable,'-m','uvicorn','backend.app.main:app','--host','127.0.0.1','--port','8002'],cwd=ROOT,env=env,stdout=log,stderr=log)
    started=time.perf_counter()
    for _ in range(100):
        if proc.poll() is not None:raise RuntimeError('isolated server exited; see load-server.log')
        try:
            if httpx.get(BASE+'/api/health',timeout=.5).status_code==200:return proc,round((time.perf_counter()-started)*1000,2)
        except httpx.HTTPError:pass
        time.sleep(.1)
    proc.terminate();raise RuntimeError('server readiness timed out')

def login(id):
    client=httpx.Client(base_url=BASE,timeout=20)
    result=client.post('/api/demo/login',json={'user_id':id});result.raise_for_status()
    client.headers['X-CSRF-Token']=result.json()['csrf_token'];return client

report={'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'transport':'real loopback HTTP','platform':platform.platform(),'python':platform.python_version(),'workers':1,'seed_cases':72,'thresholds':{'unexpected_http_errors':0,'duplicate_cases_on_retries':0,'integrity_check':'ok','latency':'observational only, no enterprise SLA or capacity claim'},'runs':[]}
report['dependencies']={name:importlib.metadata.version(name) for name in ['fastapi','starlette','pydantic','httpx']}
report['source_hashes']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'backend/app').glob('*.py')}
with tempfile.TemporaryDirectory(prefix='onboardflow-load-') as temp,(OUT/'load-server.log').open('w') as log:
    dbpath=str(Path(temp)/'load.sqlite3');env=os.environ.copy();env.update(DATABASE_PATH=dbpath,DATA_ORIGIN='test',LLM_MODE='offline',APP_MODE='demo',BIND_HOST='127.0.0.1')
    proc=None;clients=[]
    try:
        proc,start_ms=launch(env,log);report['startup_ms']=start_ms
        employee=login('emp01');admin=login('admin01');clients=[employee,admin]
        def create(i):
            d=employee.post('/api/cases/draft',json={'category':'GENERAL_ONBOARDING','fields':{'description':f'隔离压力测试办理{i}'}})
            if d.status_code!=200:return d.status_code
            dr=d.json();return employee.post('/api/cases',json={'draft_id':dr['id'],'draft_version':dr['draft_version'],'confirmation_token':dr['confirmation_token'],'confirmed':True,'allow_duplicate':True},headers={'Idempotency-Key':str(uuid.uuid4())}).status_code
        work=[('case_list',lambda i:employee.get('/api/cases').status_code,60),('grounded_query',lambda i:employee.post('/api/assistant/query',json={'question':'入职材料需要准备哪些？'}).status_code,40),('draft_and_confirm',create,20),('analytics',lambda i:admin.get('/api/analytics/summary?data_origin=test').status_code,30)]
        for name,operation,count in work:
            for concurrency in [1,4,8]:
                def timed(i):
                    begin=time.perf_counter()
                    try:status=operation(i)
                    except Exception as e:status=type(e).__name__
                    return (time.perf_counter()-begin)*1000,status
                begin=time.perf_counter()
                with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:results=list(pool.map(timed,range(count)))
                elapsed=time.perf_counter()-begin;times=[x[0] for x in results];errors=[x[1] for x in results if x[1]!=200]
                item={'scenario':name,'operations':count,'concurrency':concurrency,'elapsed_seconds':round(elapsed,3),'operations_per_second':round(count/elapsed,2),'p50_ms':pct(times,.50),'p95_ms':pct(times,.95),'p99_ms':pct(times,.99),'max_ms':round(max(times),2),'unexpected_errors':errors}
                report['runs'].append(item);print(json.dumps(item),flush=True)
        d=employee.post('/api/cases/draft',json={'category':'GENERAL_ONBOARDING','fields':{'description':'并发重试应只建一个请求'}}).json();body={'draft_id':d['id'],'draft_version':d['draft_version'],'confirmation_token':d['confirmation_token'],'confirmed':True,'allow_duplicate':True}
        key=str(uuid.uuid4())
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            responses=list(pool.map(lambda _:employee.post('/api/cases',json=body,headers={'Idempotency-Key':key}),range(16)))
        caseids={r.json().get('id') for r in responses if r.status_code==200}
        report['sixteen_way_retry']={'http_statuses':[r.status_code for r in responses],'unique_case_ids':len(caseids),'expected_unique_case_ids':1}
        probeid=next(iter(caseids));before=employee.get('/api/cases/'+probeid).json()
        # SIGKILL after observed committed result proves committed rows/sessions survive abrupt restart.
        proc.kill();proc.wait(timeout=5);proc,restart_ms=launch(env,log)
        after=employee.get('/api/cases/'+probeid);report['abrupt_restart']={'startup_ms':restart_ms,'existing_session_http_status':after.status_code,'same_case_and_timeline':after.status_code==200 and after.json()==before}
        with sqlite3.connect(dbpath) as connection:
            report['database']={'integrity_check':connection.execute('PRAGMA integrity_check').fetchone()[0],'foreign_key_violations':connection.execute('PRAGMA foreign_key_check').fetchall(),'case_count':connection.execute('SELECT COUNT(*) FROM cases').fetchone()[0],'retry_key_rows':connection.execute('SELECT COUNT(*) FROM cases WHERE idempotency_key=?',(key,)).fetchone()[0]}
        report['status']='passed' if not any(r['unexpected_errors'] for r in report['runs']) and len(caseids)==1 and all(r.status_code==200 for r in responses) and report['abrupt_restart']['same_case_and_timeline'] and report['database']['integrity_check']=='ok' and not report['database']['foreign_key_violations'] else 'failed'
    except Exception as exc:report.update(status='error',error=type(exc).__name__+': '+str(exc));raise
    finally:
        for client in clients:client.close()
        if proc and proc.poll() is None:proc.terminate();proc.wait(timeout=5)
        report['finished_at']=datetime.datetime.now(datetime.timezone.utc).isoformat();(OUT/'load_results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print('RESULT',report['status'])
