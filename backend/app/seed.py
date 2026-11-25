"""Fixed synthetic dataset, history generated through ordered state transitions."""
from datetime import datetime,timedelta,timezone
from .db import init_db,tx,uid,dump,log,unpack
BASE=datetime(2026,11,24,2,tzinfo=timezone.utc)

def seed():
    init_db()
    with tx() as c:
        if c.execute('SELECT COUNT(*) FROM users').fetchone()[0]:return False
        for i in range(1,9):
            city='广州' if i%2 else '深圳'; emp='正式员工' if i in (1,2,5,6) else '实习生'
            c.execute('INSERT INTO users VALUES (?,?,?,?,?,?,?,?)',(f'emp{i:02}',f'演示员工{i:02}','employee','xingzhou',None if i==7 else city,None if i==8 else emp,None if i>=7 else '2026-12-01',None))
        for id,name,role,city,grp in [('hr01','林悦 · 广州HR','hr','广州','广州HR'),('hr02','陈安 · 深圳HR','hr','深圳','深圳HR'),('admin01','周宁 · HR管理员','admin',None,None),('it01','许舟 · IT服务','it',None,None),('it02','叶青 · IT服务','it',None,None)]:
            c.execute('INSERT INTO users VALUES (?,?,?,?,?,?,?,?)',(id,name,role,'xingzhou',city,None,None,grp))
        from .knowledge import seed_knowledge,citations_for,resolve_context
        seed_knowledge(c)
        for i in range(72):
            person=unpack(c.execute('SELECT * FROM users WHERE id=?',(f'emp{i%6+1:02}',)).fetchone())
            category=('MATERIAL_EXCEPTION','ACCESS_DEVICE','GENERAL_ONBOARDING')[i%3]
            caseid=f'demo_case_{i+1:03}'; draftid=f'demo_draft_{i+1:03}'; group=person['city']+'HR'; hr='hr01' if person['city']=='广州' else 'hr02'
            start=BASE-timedelta(days=3+i%24,hours=i%7); sla=24 if category=='ACCESS_DEVICE' else 48
            stamp=lambda hours=0:(start+timedelta(hours=hours)).isoformat()
            fields={'description':{'MATERIAL_EXCEPTION':'入职信息表暂缺，申请延期补交','ACCESS_DEVICE':'入职演示账号尚未准备','GENERAL_ONBOARDING':'确认报到地点与联系流程'}[category]}
            if category=='MATERIAL_EXCEPTION':fields.update(missing_item='入职信息表',expected_date='2026-12-05')
            if category=='ACCESS_DEVICE':fields['device_type']='账号'
            refs=citations_for(c,person,category)
            c.execute('INSERT INTO cases (id,case_number,requester_id,category,status,assigned_group,assigned_to,description,structured_fields,policy_snapshot_refs,context_source,created_at,updated_at,due_at,idempotency_key,sla_hours,data_origin) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(caseid,f'OF-DEMO-{i+1:03}',person['id'],category,'new',group,hr,fields['description'],dump(fields),dump(refs),dump(resolve_context(c,person)),stamp(),stamp(),stamp(sla),f'seed-key-{i}',sla,'demo'))
            c.execute('INSERT INTO drafts VALUES (?,?,?,?,?,?,?,?,?,?)',(draftid,person['id'],category,1,dump(resolve_context(c,person)),dump(fields),stamp(),stamp(),caseid,'seed-token'))
            for attempt in range(1,3 if i%4==0 else 2):
                invalid=attempt==1 and i%4==0
                c.execute('INSERT INTO submission_attempts VALUES (?,?,?,?,?,?,?,?,?,?,?)',(f'seed_attempt_{i}_{attempt}',draftid,person['id'],attempt,stamp(), 'invalid' if invalid else 'valid',dump(['expected_date' if category=='MATERIAL_EXCEPTION' else 'description'] if invalid else []),f'seed-attempt-key-{i}-{attempt}','seed',None if invalid else caseid,'demo'))
            log(c,'case_draft_created',person['id'],metadata={'draft_id':draftid,'category':category},occurred_at=stamp())
            log(c,'case_submitted',person['id'],caseid,{'draft_id':draftid,'category':category},occurred_at=stamp())
            state='new'
            def move(to,hour):
                nonlocal state
                log(c,'status_changed',hr,caseid,{'from_status':state,'to_status':to},occurred_at=stamp(hour))
                c.execute('UPDATE cases SET status=?,updated_at=? WHERE id=?',(to,stamp(hour),caseid));state=to
            def reply(hour,text):
                c.execute('INSERT INTO messages VALUES (?,?,?,?,?,?)',(f'seed_msg_{i}_{hour}',caseid,hr,text,'public',stamp(hour)))
                log(c,'hr_public_response',hr,caseid,{'body':text},occurred_at=stamp(hour))
                c.execute('UPDATE cases SET first_human_response_at=COALESCE(first_human_response_at,?) WHERE id=?',(stamp(hour),caseid))
            move('assigned',0)
            bucket=i%8
            if category=='MATERIAL_EXCEPTION':c.execute('INSERT INTO materials VALUES (?,?,?,?,?,?)',(f'seed_mat_{i}',caseid,'onboarding_form',0,'missing',None))
            if bucket!=0:
                move('in_progress',1)
                if bucket in (1,2):
                    reply(2,'请补充预计准备时间，便于安排后续办理。');move('waiting_employee',2)
                else:
                    reply(2,'已接单，正在核对适用制度与办理依赖。')
                if category=='ACCESS_DEVICE':
                    tid=f'seed_task_{i}'; completed=bucket in (3,4,5,7)
                    c.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?)',(tid,caseid,'IT_ACCESS','it01' if i%2 else 'it02','pending',1,'[]',stamp(24),None,dump({'id':person['id'],'name':person['name'],'city':person['city'],'planned_start_date':person['planned_start_date'],'instruction':'演示账号准备'}),1))
                    log(c,'task_created',hr,caseid,{'task_id':tid,'type':'IT_ACCESS','required':True},occurred_at=stamp(1.5))
                    if completed:
                        c.execute("UPDATE tasks SET status='completed',completed_at=?,row_version=2 WHERE id=?",(stamp(4),tid))
                        log(c,'task_completed','it01' if i%2 else 'it02',caseid,{'task_id':tid,'result':'演示任务已完成'},occurred_at=stamp(4))
                if bucket==6:
                    move('cancelled',4);c.execute('UPDATE cases SET cancelled_at=? WHERE id=?',(stamp(4),caseid));c.execute("UPDATE tasks SET status='cancelled' WHERE case_id=? AND status='pending'",(caseid,))
                if bucket in (3,4,5,7):
                    hours=60 if bucket==5 else 8
                    reply(hours,'本次模拟补交安排已批准；材料仍待实际补交。' if category=='MATERIAL_EXCEPTION' else '演示任务已完成，请核对结果。')
                    code='approved_deferred' if category=='MATERIAL_EXCEPTION' else 'answered'
                    if category=='MATERIAL_EXCEPTION':
                        fields['approved_expected_date']='2026-12-05'
                        c.execute('UPDATE cases SET structured_fields=? WHERE id=?',(dump(fields),caseid))
                        tid=f'seed_followup_{i}'
                        c.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?)',(tid,caseid,'MATERIAL_FOLLOWUP',hr,'pending',0,'[]','2026-12-05T15:59:59+00:00',None,dump({'instruction':'跟进入职信息表元信息'}),1))
                        log(c,'task_created',hr,caseid,{'task_id':tid,'type':'MATERIAL_FOLLOWUP','required':False},occurred_at=stamp(hours))
                    move('resolved',hours);c.execute('UPDATE cases SET latest_resolved_at=?,resolution_code=? WHERE id=?',(stamp(hours),code,caseid))
                    if bucket in (4,7):move('closed',hours+1);c.execute('UPDATE cases SET closed_at=? WHERE id=?',(stamp(hours+1),caseid))
                    if bucket==7:
                        move('in_progress',hours+2);c.execute('UPDATE cases SET closed_at=NULL,reopen_count=1 WHERE id=?',(caseid,))
                    if bucket==4:
                        c.execute('INSERT INTO feedback VALUES (?,?,?,?,?,?,?,?)',(f'seed_feedback_{i}',caseid,None,person['id'],3+i%3,'resolved',stamp(hours+2),'demo'))
                        log(c,'feedback_submitted',person['id'],caseid,{'ease':3+i%3,'outcome':'resolved'},occurred_at=stamp(hours+2))
            # Structured synthetic consultation events, separate from case messages.
            conv=f'seed_conversation_{i}'
            meta={'conversation_id':conv,'category':category,'provider_mode':'offline','question':fields['description']}
            log(c,'question_submitted',person['id'],metadata=meta,occurred_at=stamp(-1))
            log(c,'answer_shown',person['id'],metadata={**meta,'action':'offer_handoff' if i%5==0 else 'answer','reason_code':'no_match' if i%5==0 else 'matched'},occurred_at=stamp(-1))
        return True

if __name__=='__main__':print('已初始化模拟数据' if seed() else '数据库已有数据，未重复初始化')
