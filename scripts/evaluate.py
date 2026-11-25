"""Run public synthetic acceptance tasks against isolated SQLite + real ASGI app.
Query tasks use their input verbatim; workflow/boundary tasks execute named API
protocols, whose observed states are asserted. Not a natural-language agent benchmark.
"""
import csv
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import traceback
from datetime import datetime,timezone
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['APP_MODE']='demo'
os.environ['BIND_HOST']='127.0.0.1'
os.environ['DATA_ORIGIN']='test'
os.environ['LLM_MODE']='offline'
from fastapi.testclient import TestClient
from backend.app import db,knowledge,providers
from backend.app.main import app
from backend.tests.test_cases import login,draft,submit,command,create,accept
from backend.tests import test_cases as business
from backend.tests import test_knowledge as knowledge_tests


def ok(response):
    assert response.status_code==200, f'HTTP {response.status_code}: {response.text}'
    return response.json()


def fixtures(row):
    with db.tx() as conn:
        conn.execute('UPDATE users SET planned_start_date=? WHERE id=?',(row['planned_start_date'],row['user_id']))
        for field in row.get('missing_fields',[]):
            assert field in ('city','employment_type','planned_start_date')
            conn.execute(f'UPDATE users SET {field}=NULL WHERE id=?',(row['user_id'],))
        fixture=row.get('fixture')
        if fixture=='future_publication': conn.execute("UPDATE policies SET published_at='2027-01-01T00:00:00+00:00' WHERE status='published'")
        if fixture=='all_expired': conn.execute("UPDATE policies SET effective_to='2026-11-01' WHERE status='published'")
        if fixture=='only_hr': conn.execute("UPDATE policies SET visibility_roles='[\"hr\",\"admin\"]'")
        if fixture=='legacy_conflict':
            p=knowledge.decode(conn.execute("SELECT * FROM policies WHERE id='pol-material-gz-full'").fetchone())
            p.pop('policy_id',None)
            p.update(id='legacy-conflict',version='import-conflict',title='低相关性冲突夹具',content_markdown='与报到地点无词面关系的遗留导入文章。')
            knowledge.insert_policy(conn,p)


def query_task(c,row):
    login(c,row['user_id'])
    result=ok(c.post('/api/assistant/query',json={'question':row['input']}))
    assert result['action']==row['expected_action'], f"expected {row['expected_action']}, got {result['action']}: {result['answer_text']}"
    refs={r['article_version_id'] for r in result['citation_refs']}
    assert set(row['expected_article_versions']) <= refs, f"missing required versions {set(row['expected_article_versions'])-refs}"
    for fact in row['required_facts']: assert fact in result['answer_text'], f'missing fact: {fact}'
    for fact in row['forbidden_facts']: assert fact not in result['answer_text'], f'forbidden fact: {fact}'
    if row.get('reason'): assert result['reason_code']==row['reason'], f"reason {result['reason_code']}"
    if row.get('required_context_fields'): assert set(result['required_context_fields'])==set(row['required_context_fields'])
    if row.get('expected_case_category'): assert result['category']==row['expected_case_category']
    for ref in result['citation_refs']:
        doc=ok(c.get(ref['url']))
        assert doc['id']==ref['article_version_id'] and ref['excerpt'] in doc['content_markdown']
    return {'action':result['action'],'reason_code':result['reason_code'],'citation_ids':sorted(refs),'answer_text':result['answer_text'],'provider_mode':result['provider_mode'],'semantic_review':'pending_human_review'}


def make_access(c):
    return create(c,'ACCESS_DEVICE',{'description':'评价：账号设备未准备好','device_type':'账号'})


def workflow(c,row):
    name=row['scenario']
    if name=='draft_preview':
        login(c); before=len(ok(c.get('/api/cases'))); d=draft(c)
        assert len(ok(c.get('/api/cases')))==before and not d['case_id']
        return {'action':'draft','draft_id':d['id'],'new_cases':0}
    if name in ('material_closed','material_followup'):
        ca=accept(c,create(c)); ca=ok(command(c,ca,'resolve',body='批准演示延期，材料仍需补交。',resolution_code='approved_deferred',expected_date='2027-01-02'))
        assert ca['materials'][0]['provided']==0 and ca['tasks'][0]['status']=='pending' and ca['tasks'][0]['required']==0
        login(c); ca=ok(command(c,ca,'confirm'))
        if name=='material_followup':
            ca=ok(c.post(f"/api/cases/{ca['id']}/materials",json={'row_version':ca['row_version'],'required_item_code':'入职信息表','provided':True}))
            assert ca['materials'][0]['provided']==1 and ca['tasks'][0]['status']=='completed'
        else: assert ca['materials'][0]['provided']==0
        assert ca['resolution_code']=='approved_deferred'
    elif name=='supplement':
        ca=accept(c,create(c)); ca=ok(command(c,ca,'ask_employee',body='请补充缺失原因。')); assert ca['status']=='waiting_employee'
        login(c); ca=ok(c.post(f"/api/cases/{ca['id']}/messages",json={'row_version':ca['row_version'],'body':'模拟材料需要重新整理。'}))
    elif name=='general':
        ca=accept(c,create(c,'GENERAL_ONBOARDING',{'description':'请确认报到地点'}))
        assert ca['policy_snapshot_refs'];ca=ok(command(c,ca,'resolve',body='依据适用报到制度，请按计划日期到广州演示园区 A 座 2 楼。'))
    else:
        ca=make_access(c)
        if name=='progress':
            result=ok(c.post('/api/assistant/query',json={'question':'我的账号设备准备进度怎么样？'}))
            assert result['action']=='show_status' and ca['id'] in [x['id'] for x in result['cases']]
        else:
            ca=accept(c,ca); ca=ok(command(c,ca,'create_task',assignee_id='it01',task_type='IT_ACCESS')); t=ca['tasks'][0]
            if name=='required_block':
                r=command(c,ca,'resolve',body='试图跳过任务')
                assert r.status_code==400 and r.json()['detail']['code']=='pending_tasks'
                ca=ok(c.get(f"/api/cases/{ca['id']}"))
            elif name=='cancel':
                ca=ok(command(c,ca,'cancel',reason='模拟取消原因'))
                assert ca['tasks'][0]['status']=='cancelled'
                login(c,'it01'); assert c.post(f"/api/tasks/{t['id']}/actions/complete",json={'row_version':2}).status_code==400
            else:
                login(c,'it01'); ok(c.post(f"/api/tasks/{t['id']}/actions/complete",json={'row_version':1}))
                login(c,'hr01'); ca=ok(c.get(f"/api/cases/{ca['id']}")); ca=ok(command(c,ca,'resolve',body='演示任务已完成，请核对。'))
                login(c); ca=ok(command(c,ca,'confirm'))
                if name=='reopen':
                    due=ca['due_at'];ca=ok(command(c,ca,'reopen',reason='还需核对模拟设备'))
                    assert ca['due_at']==due and ca['reopen_count']==1 and ca['tasks'][0]['status']=='completed'
    assert ca['category']==row['expected_case_category'], f"category {ca['category']}"
    assert ca['status']==row['expected_final_state'],f"expected final {row['expected_final_state']}, got {ca['status']}"
    return {'action':row['expected_action'],'case_id':ca['id'],'final_state':ca['status'],'category':ca['category'],'provided':[m['provided'] for m in ca.get('materials',[])]}


def boundary(c,row):
    name=row['scenario']
    handlers={'object_acl':business.test_cross_user_group_and_it_authorization,'idempotency':business.test_draft_confirmation_idempotence_and_payload_conflict,
              'concurrency':business.test_cancel_atomic_task_lifecycle_and_stale_update,'csrf':business.test_csrf_schema_and_persistence,
              'scope_overlap':knowledge_tests.test_publish_partial_scope_overlap_and_wildcard}
    if name in handlers:
        handlers[name](c)
        return {'assertion_protocol':handlers[name].__name__,'completed':True}
    if name=='prompt_injection':
        login(c); result=ok(c.post('/api/assistant/query',json={'question':row['input']}))
        assert 'pol-hr-internal' not in [r['article_version_id'] for r in result['citation_refs']]
        assert c.get('/api/knowledge/pol-hr-internal').status_code==403
        assert ok(c.get('/api/me'))['user']['role']=='employee'
        return {'action':result['action'],'no_private_citations':True,'role':'employee'}
    # Inject provider failure on a real query, then prove ordinary request creation still works.
    import httpx
    baseline_environment={'LLM_MODE':'remote','LLM_API_KEY':'fake-test-only','LLM_ENDPOINT':'https://invalid.example/model','LLM_MODEL':'fixture'}
    if name=='remote_missing': baseline_environment['LLM_API_KEY']=''
    def fake_post(*args,**kwargs):
        if name=='remote_timeout': raise httpx.TimeoutException('synthetic timeout')
        content='not json' if name=='remote_json' else json.dumps({'action':'answer','answer_text':'伪造引用输出','citation_ids':['private-hallucinated-policy']})
        return httpx.Response(200,json={'choices':[{'message':{'content':content}}]},request=httpx.Request('POST','https://invalid.example/model'))
    with patch.dict(os.environ,baseline_environment),patch.object(providers.httpx,'post',side_effect=fake_post):
        login(c);result=ok(c.post('/api/assistant/query',json={'question':'入职材料清单是什么？'}))
        assert result['provider_mode']=='offline_fallback' and result['citation_refs'] and result['action']=='answer'
        assert '伪造引用输出' not in result['answer_text']
        ca=ok(submit(c,draft(c)))
        assert ca['status']=='assigned' and ok(c.get(f"/api/cases/{ca['id']}"))['id']==ca['id']
    return {'provider_mode':result['provider_mode'],'provider_failure':result.get('provider_failure'),'final_state':ca['status'],'external_network_called':False}


def main():
    import argparse
    parser=argparse.ArgumentParser(description='Run public synthetic task evaluation in isolated databases')
    parser.add_argument('--output-dir',default=str(ROOT/'reports'))
    args=parser.parse_args()
    output_dir=Path(args.output_dir)
    if not output_dir.is_absolute():output_dir=ROOT/output_dir
    output_dir.mkdir(parents=True,exist_ok=True)
    dataset=json.loads((ROOT/'data/eval_acceptance/tasks.json').read_text())
    development=json.loads((ROOT/'data/eval_dev/tasks.json').read_text())
    assert not ({r['family_id'] for r in dataset['tasks']} & {r['family_id'] for r in development['tasks']}), 'Family leakage between splits'
    dev_results=[]
    for row in development['tasks']:
        with tempfile.TemporaryDirectory(prefix='onboardflow-dev-') as td,patch.dict(os.environ,{'DATABASE_PATH':str(Path(td)/'isolated.sqlite3')}),patch.object(db,'_clock',lambda:datetime.fromisoformat(row['now'])):
            with TestClient(app) as client:
                fixtures(row); login(client,row['user_id'])
                response=client.post('/api/assistant/query',json={'question':row['input']})
                dev_results.append({'test_id':row['test_id'],'family_id':row['family_id'],'input':row['input'],'http_status':response.status_code,'observed':response.json(),'label_validation':'pending_human_review'})
    (output_dir/'development_observations.json').write_text(json.dumps({'run_at':datetime.now(timezone.utc).isoformat(),'dataset_version':development['dataset_version'],'family_disjoint_with_acceptance':True,'accuracy':None,'accuracy_note':'Exploratory labels not validated; this report records observations only. No threshold changed by this runner.','results':dev_results},ensure_ascii=False,indent=2)+'\n')
    started=datetime.now(timezone.utc).isoformat(); results=[]
    for row in dataset['tasks']:
        result={'test_id':row['test_id'],'family_id':row['family_id'],'group':row['group'],'input':row['input']}
        try:
            with tempfile.TemporaryDirectory(prefix='onboardflow-eval-') as td,patch.dict(os.environ,{'DATABASE_PATH':str(Path(td)/'isolated.sqlite3')}),patch.object(db,'_clock',lambda:datetime.fromisoformat(row['now'])):
                with TestClient(app) as client:
                    fixtures(row)
                    observed=(workflow if row['group']=='workflow' else boundary if row['group']=='boundary' else query_task)(client,row)
            result.update(passed=True,observed=observed,failure_reason=None)
        except Exception as exc:
            result.update(passed=False,failure_reason=f'{type(exc).__name__}: {exc}',traceback=traceback.format_exc())
        results.append(result)
        print(f"{result['test_id']} {'PASS' if result['passed'] else 'FAIL'} {result.get('failure_reason') or ''}")
    groups={g:{'passed':sum(x['passed'] for x in results if x['group']==g),'total':sum(x['group']==g for x in results)} for g in ('answer','clarify','handoff','workflow','boundary')}
    commit=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,capture_output=True,text=True)
    report={'run_started_at':started,'run_finished_at':datetime.now(timezone.utc).isoformat(),'commit':commit.stdout.strip() if commit.returncode==0 else None,
            'platform':platform.platform(),'python':platform.python_version(),'dependencies':{x:importlib.metadata.version(x) for x in ['fastapi','pydantic','httpx','pytest']},
            'dataset_version':dataset['dataset_version'],'dataset_kind':'developer-authored public synthetic acceptance, not blind',
            'provider_mode':'offline; remote failure injections only','real_remote_provider':'not_run_no_credentials',
            'configuration':{'clock':'per-task injected UTC','database':'fresh isolated SQLite per task','data_origin':'test','retrieval':knowledge.RETRIEVAL_CONFIG},
            'groups':groups,'total':len(results),'passed':sum(x['passed'] for x in results),'failed':sum(not x['passed'] for x in results),
            'semantic_accuracy':'pending_human_review','known_limits':['自动断言与事实字符串仅是筛查，不是语义准确率。','办理任务运行固定 API 协议，不是自然语言自主办理成功率。','公开合成开发/验收集不是独立盲测。','真实用户访谈、业务收益及真实远程模型均未验证。','无依据的一般入职请求可转人工并公开回复；正式解决仍需要合规制度依据，不能为办结而编造依据。'], 'results':results}
    (output_dir/'evaluation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    lines=['# 实际合成验收结果','',f"运行：{started}",f"实际通过 **{report['passed']}/{report['total']}**；失败 {report['failed']}。",'','默认离线，远程部分只注入故障；真实远程模型未运行。开发者公开合成集，不是独立盲测。自动筛查不代表完整语义准确率。','','|类别|通过|总量|','|---|---:|---:|']
    lines += [f'|{g}|{v["passed"]}|{v["total"]}|' for g,v in groups.items()]
    lines+=['','## 失败案例','']+[f'- {x["test_id"]}：{x["failure_reason"]}' for x in results if not x['passed']]
    if not report['failed']:lines+=['本次自动筛查未发现失败；不据此声称真实用户任务全部成功或真实收益。','首轮 59/60：ACC-038 冲突夹具误将序列化别名 policy_id 当作数据库字段，导致执行器错误；修复夹具后重跑 60/60，未修改验收标签。首轮原始报告保留在 evaluation_initial.json。']
    lines+=['','## 人工语义复核','', '尚未执行。请使用 semantic_review.csv，按事实正确、适用条件正确、无额外无依据主张、下一步可执行四项复核。引用 ID 可打开只证明引用对象合法，不能代替语义支持判断。','','预设目标：关键边界全部通过、可答自动筛查至少 90%。以上表格才是实际结果，真实业务耗时与收益留空。']
    (output_dir/'evaluation.md').write_text('\n'.join(lines)+'\n')
    with (output_dir/'semantic_review.csv').open('w',newline='') as handle:
        writer=csv.writer(handle);writer.writerow(['test_id','input','fact_correct','scope_correct','no_unsupported_claim','actionable_next_step','citation_semantic_support','reviewer','review_status','notes'])
        for row in dataset['tasks'][:40]:writer.writerow([row['test_id'],row['input'],'','','','','','','待人工复核',''])
    return 1 if report['failed'] else 0

if __name__=='__main__':sys.exit(main())
