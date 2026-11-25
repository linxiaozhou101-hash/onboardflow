import {test,expect} from '@playwright/test';
import {Diary,actor,ask,createCase,openCase,closeDialog,accept,refreshCase,readCase,futureDate,publicReply} from './helpers';

/** Role enactment: no business POST is made through the API test client.
 * API GETs corroborate visible results; dates/ratings are synthetic scenario data.
 */
test('R01 广州正式员工：补交咨询、HR补问、催问、延期审批、确认后实际补交',async({browser})=>{
 const diary=new Diary('R01');const employee=await actor(browser,'emp01',true);const hr=await actor(browser,'hr01');
 const e=employee.page,h=hr.page;const due=futureDate(7);let id='';
 const title='R01拟真：联系信息待确认，入职信息表申请延期';
 const internal='R01仅HR：在今天交接时确认回访安排，不向员工暴露内部排班。';
 try{
  await diary.step('员工 emp01（广州正式，手机）','报到前一周','问入职信息表缺失能否补交并查看制度',async()=>{
   const text=await ask(e,'我的入职信息表还缺联系信息，可以提交补交申请吗？');
   expect(text).toContain('审批');expect(text).toMatch(/不代表|不等于/);await expect(e.locator('.answer-card .citations button').first()).toBeVisible();
   await e.locator('.answer-card .citations button').first().click();await expect(e.getByRole('dialog')).toContainText('仅用于作品演示');await closeDialog(e);
   return '能看到申请须审批、不能视为已获批的说明，并打开引用原文。';
  });
  await diary.step('员工 emp01','提交申请','填缺项和预计日期、预览并明确确认新事项',async()=>{
   const ca=await createCase(e,title,'MATERIAL_EXCEPTION',{missing_item:'入职信息表',expected_date:due});id=ca.id;
   expect(ca.status).toBe('assigned');expect(ca.assigned_group).toBe('广州HR');expect(ca.materials[0].provided).toBe(false);
   await openCase(e,title);await expect(e.locator('.detail-owner')).toContainText('hr01');await diary.photo(e,'employee-submitted');
   return `已生成${ca.case_number}，广州HR负责；材料仍缺失，不会因申请已提交而显示收齐。`;
  });
  await diary.step('HR hr01','晨间接单','接单、留内部交接备注，再向员工提出具体补问',async()=>{
   await openCase(h,title);await accept(h);
   await h.getByLabel('HR 内部备注（员工不可见）').fill(internal);await h.getByRole('button',{name:'保存内部备注',exact:true}).click();await expect(h.locator('.messages')).toContainText(internal);
   const noteOnly=await readCase(h,id);expect(noteOnly.first_human_response_at).toBeNull();
   await publicReply(h,'请补充暂未准备好的具体原因，并确认预计补交日期。我们收到说明后再审核延期安排。','请员工补充信息');
   await expect(h.locator('.detail-summary')).toContainText('待补充信息');
   return '内部备注不构成人工公开回复；补问后进入待员工补充。';
  });
  await diary.step('员工 emp01','等待过程中焦虑','先询问进度，尚未回答HR要求的原因',async()=>{
   await refreshCase(e);await expect(e.locator('.detail-summary')).toContainText('待补充信息');await expect(e.getByRole('dialog')).not.toContainText(internal);
   await e.getByLabel('补充信息 / 回复').fill('我明天还要确认报到安排，请问现在处理到哪一步？原因信息我还没拿到。');
   const response=e.waitForResponse(r=>r.url().endsWith(`/cases/${id}/messages`)&&r.request().method()==='POST');await e.getByRole('button',{name:'发送为主动进度询问',exact:true}).click();const result=await(await response).json();
   diary.entries.push({actor:'观察员',scene:'催问后状态实测',action:'读取真实消息响应',observation:`催问类型progress_inquiry后status=${result.status}；预期仍waiting_employee，因未补充所问内容。`,wall_time:new Date().toISOString()});diary.save();
   await diary.photo(e,'progress-inquiry-state');expect(result.status).toBe('waiting_employee');
   expect(result.events.filter((x:any)=>x.kind==='progress_inquiry')).toHaveLength(1);
   return '催问已记录；仍明确等待员工补充，不把催问当作材料或信息补齐。';
  });
  await diary.step('员工 emp01','拿到可说明的信息','提交真实补充说明',async()=>{
   await e.getByLabel('补充信息 / 回复').fill(`联系信息需要学校确认，今天已联系学校；预计${due}补齐入职信息表，不申请免交。`);
   await e.getByRole('button',{name:'发送补充信息',exact:true}).click();await expect(e.locator('.detail-summary')).toContainText('处理中');
   expect((await readCase(e,id)).materials[0].provided).toBe(false);return '实质说明提交后恢复HR处理；材料元信息仍未提供。';
  });
  await diary.step('HR hr01','审批环节','根据说明批准一次延期，并保留实际补交待办',async()=>{
   await refreshCase(h);await expect(h.locator('.detail-summary')).toContainText('处理中');
   await h.getByLabel('员工可见回复').fill(`批准本次延期补交安排，请于${due}记录补交。本次完成的是申请审批，材料尚未收齐。`);
   await h.getByLabel('审批原因').fill('员工说明联系信息等待学校确认，已经给出可跟进的补交日期。');await h.getByLabel('约定补交日期').fill(due);
   await h.getByRole('button',{name:'处理完毕，交员工确认',exact:true}).click();await expect(h.locator('.detail-summary')).toContainText('待员工确认');
   const ca=await readCase(h,id);expect(ca.resolution_code).toBe('approved_deferred');expect(ca.materials[0].provided).toBe(false);expect(ca.tasks.find((t:any)=>t.type==='MATERIAL_FOLLOWUP')).toMatchObject({required:false,status:'pending'});
   await diary.photo(h,'hr-approved-awaiting-material');return '延期申请已处理，材料缺失和非必需后续待办同时保留。';
  });
  await diary.step('员工 emp01','误解完成含义','追问批准后是否已经收齐，再确认审批结果',async()=>{
   await closeDialog(e);const text=await ask(e,'HR批准入职信息表补交申请后，材料是不是已经收齐？');expect(text).toMatch(/不代表|不等于/);
   await openCase(e,title);await expect(e.getByRole('dialog')).toContainText('申请处理完毕不代表材料收齐');await e.getByRole('button',{name:'确认处理结果',exact:true}).click();await expect(e.locator('.detail-summary')).toContainText('已确认完成');
   const ca=await readCase(e,id);expect(ca.status).toBe('closed');expect(ca.materials[0].provided).toBe(false);expect(ca.tasks.find((t:any)=>t.type==='MATERIAL_FOLLOWUP').status).toBe('pending');
   await diary.photo(e,'application-closed-material-pending');return '员工只确认审批结果；已确认完成的申请仍有待补交材料，页面有解释。';
  });
  await diary.step('员工 emp01','剧情推进至实际准备好后（未伪造系统时钟）','结案后记录材料已提供，并查看清单',async()=>{
   await e.getByRole('button',{name:'记录已补交',exact:true}).click();await expect(e.locator('.material-row')).toContainText('已完成');
   const ca=await readCase(e,id);expect(ca.status).toBe('closed');expect(ca.resolution_code).toBe('approved_deferred');expect(ca.materials[0].provided).toBe(true);expect(ca.tasks.find((t:any)=>t.type==='MATERIAL_FOLLOWUP').status).toBe('completed');
   await e.getByRole('dialog').getByLabel('容易程度').selectOption('4');await e.getByRole('dialog').getByLabel('问题解决情况').selectOption('resolved');await e.getByRole('dialog').getByRole('button',{name:'提交反馈',exact:true}).click();await expect(e.getByRole('dialog')).toContainText('感谢反馈');
   await closeDialog(e);await expect(e.locator('.checklist-items > div').filter({hasText:'入职信息表'})).toContainText('已记录提供状态');await diary.photo(e,'supplied-checklist');
   return '实际补交才更新材料和跟进待办；原审批结论不变。4分/已解决是明确的合成测试输入，不是真人评分。';
  });
 }finally{diary.save();await employee.context.close();await hr.context.close();}
});

test('R02 广州实习生：延期被拒、给出负面反馈、补充新证据重开后修正',async({browser})=>{
 const diary=new Diary('R02');const employee=await actor(browser,'emp03');const hr=await actor(browser,'hr01');const admin=await actor(browser,'admin01');
 const e=employee.page,h=hr.page,a=admin.page;const due=futureDate(5);const title='R02拟真：实习安排未最终确认，申请补交';let id='';let firstResolved='';let originalDue='';
 try{
  await diary.step('实习生 emp03','入职前遇到材料异常','查询实习生材料后创建补交申请',async()=>{
   const text=await ask(e,'广州实习生入职材料清单有哪些？');expect(text).toContain('实习安排确认');
   const ca=await createCase(e,title,'MATERIAL_EXCEPTION',{missing_item:'实习安排确认',expected_date:due});id=ca.id;originalDue=ca.due_at;return `请求${ca.case_number}已分派广州HR，实习安排确认仍缺失。`;
  });
  await diary.step('HR hr01','首次审查','解释暂不批准原因并给出可执行下一步',async()=>{
   await openCase(h,title);await accept(h);await h.getByLabel('员工可见回复').fill('本次暂不批准延期：目前没有确认的补交安排。请先取得学校的具体答复，再在原请求中说明新情况申请重新处理。');await h.getByLabel('补交审批结果').selectOption('rejected');await h.getByLabel('审批原因').fill('尚缺可核实的补交安排，当前无法同意本次模拟延期。');
   await h.getByRole('button',{name:'处理完毕，交员工确认',exact:true}).click();await expect(h.locator('.detail-summary')).toContainText('待员工确认');
   const ca=await readCase(h,id);firstResolved=ca.latest_resolved_at;expect(ca.resolution_code).toBe('rejected');expect(ca.materials[0].provided).toBe(false);expect(ca.tasks.filter((t:any)=>t.type==='MATERIAL_FOLLOWUP')).toHaveLength(0);
   return '拒绝结论和原因员工可见；没有把拒绝当成材料已齐，也没有创建虚假的已完成跟进。';
  });
  await diary.step('实习生 emp03','第一次结果未解决实际困境','如实提交负面反馈，取得新信息后重开',async()=>{
   await openCase(e,title);await expect(e.locator('.messages')).toContainText('暂不批准');
   await e.getByRole('dialog').getByLabel('容易程度').selectOption('1');await e.getByRole('dialog').getByLabel('问题解决情况').selectOption('unresolved');await e.getByRole('dialog').getByRole('button',{name:'提交反馈',exact:true}).click();await expect(e.getByRole('dialog')).toContainText('感谢反馈');
   await e.getByLabel('重开原因').fill(`学校刚确认会在${due}给出实习安排，我希望基于这条新信息重新审核，不是重复问相同问题。`);await e.getByRole('button',{name:'申请重新处理',exact:true}).click();await expect(e.locator('.detail-summary')).toContainText('处理中');
   const ca=await readCase(e,id);expect(ca.reopen_count).toBe(1);expect(ca.due_at).toBe(originalDue);expect(ca.latest_resolved_at).toBe(firstResolved);expect(ca.materials[0].provided).toBe(false);
   await diary.photo(e,'reopened-with-new-information');return '合成输入1分/未解决保存后，再以新证据重开；这不是人类满意度数据。';
  });
  await diary.step('管理员 admin01','同时观察服务质量','重开期间检查已有负面反馈仍参与统计',async()=>{
   const rows=await(await a.request.get('/api/analytics/details?metric=ease_score&data_origin=test')).json();const feedback=rows.rows.find((r:any)=>r.case_id===id);expect(feedback).toMatchObject({ease:1,outcome:'unresolved'});
   await a.getByRole('link',{name:'服务分析',exact:true}).click();await a.getByLabel('数据来源').selectOption('test');await expect(a.locator('.metric-card').filter({hasText:'容易程度评分'})).toBeVisible();await a.locator('.metric-card').filter({hasText:'容易程度评分'}).click();await expect(a.getByRole('dialog')).toContainText(id);await diary.photo(a,'negative-feedback-retained');await closeDialog(a);
   return '真实记录验证：请求重开没有移除已有1分负面反馈。管理员画面仅查看测试数据。';
  });
  await diary.step('HR hr01','看到新信息后重新办理','基于新证据批准延期并明确剩余义务',async()=>{
   await refreshCase(h);await expect(h.locator('.messages')).toContainText('学校刚确认');await h.getByLabel('员工可见回复').fill(`根据学校新确认的信息，修正本次审批为批准延期至${due}。请届时实际补交实习安排确认，材料仍未收齐。`);await h.getByLabel('补交审批结果').selectOption('approved_deferred');await h.getByLabel('约定补交日期').fill(due);await h.getByLabel('审批原因').fill('新信息给出明确补交安排，需继续跟进实际提交。');
   await h.getByRole('button',{name:'处理完毕，交员工确认',exact:true}).click();await expect(h.locator('.detail-summary')).toContainText('待员工确认');const ca=await readCase(h,id);expect(ca.resolution_code).toBe('approved_deferred');expect(ca.reopen_count).toBe(1);expect(ca.materials[0].provided).toBe(false);expect(ca.tasks.filter((t:any)=>t.type==='MATERIAL_FOLLOWUP')).toHaveLength(1);
   expect(ca.messages.some((m:any)=>m.body.includes('暂不批准'))).toBe(true);return '新审批覆盖当前结论，但首次拒绝解释、负面反馈、重开原因均保留。';
  });
  await diary.step('实习生 emp03','确认修改后的处理结果','确认申请并核对材料仍待补交',async()=>{
   await refreshCase(e);await e.getByRole('button',{name:'确认处理结果',exact:true}).click();await expect(e.locator('.detail-summary')).toContainText('已确认完成');const ca=await readCase(e,id);expect(ca.status).toBe('closed');expect(ca.materials[0].provided).toBe(false);expect(ca.tasks.find((t:any)=>t.type==='MATERIAL_FOLLOWUP').status).toBe('pending');await diary.photo(e,'revised-approval-confirmed');
   const feedback=await(await a.request.get('/api/analytics/details?metric=ease_score&data_origin=test')).json();expect(feedback.rows.find((r:any)=>r.case_id===id)).toMatchObject({ease:1,outcome:'unresolved'});return '员工确认新的延期安排；尚未实际补交，初次负面反馈没有被覆盖或删除。';
  });
 }finally{diary.save();await employee.context.close();await hr.context.close();await admin.context.close();}
});
