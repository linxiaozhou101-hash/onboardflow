import {test,expect} from '@playwright/test';
import type {Page} from '@playwright/test';
import {Diary,actor,openCase,refreshCase,accept,ask,createCase,readCase,publicReply,closeDialog,futureDate} from './helpers';

async function supplyMissingContext(page:Page){
  if(await page.locator('.context-form').isVisible()){
    await page.locator('.context-form').getByLabel('工作城市').selectOption('深圳');
    await page.locator('.context-form').getByLabel('计划入职日',{exact:true}).fill(futureDate(12));
    const answer=page.waitForResponse(r=>r.url().endsWith('/api/assistant/query')&&r.request().method()==='POST');
    await page.getByRole('button',{name:'确认自报信息并继续',exact:true}).click();
    expect((await answer).status()).toBe(200);await expect(page.locator('.context-form')).toHaveCount(0);
  }
}
async function knowledge(page:Page){await page.getByRole('link',{name:'制度知识库',exact:true}).click();await page.getByLabel('搜索制度').fill('report-sz');}
function policyRow(page:Page,version:string){return page.locator('tbody tr').filter({has:page.getByText('report-sz · v'+version,{exact:true})});}
async function summary(page:Page){const r=await page.request.get('/api/analytics/summary?data_origin=test');expect(r.status()).toBe(200);return r.json();}
async function detail(page:Page,metric:string){const r=await page.request.get('/api/analytics/details?data_origin=test&metric='+metric);expect(r.status()).toBe(200);return r.json();}
async function showAnalytics(page:Page){await page.getByRole('link',{name:'服务分析',exact:true}).click();await page.getByLabel('数据来源').selectOption('test');await expect(page.locator('.metric-grid')).toBeVisible();}
async function savePolicy(page:Page){const response=page.waitForResponse(r=>r.url().includes('/api/admin/knowledge/')&&['POST','PUT'].includes(r.request().method()));await page.getByRole('button',{name:'保存草稿',exact:true}).click();const r=await response;expect(r.status()).toBe(200);await expect(page.getByRole('dialog')).toHaveCount(0);return r.json();}

test('R05 档案缺城市与日期的实习生，经澄清后进入深圳HR服务闭环',async({browser})=>{
 const diary=new Diary('R05');const employee=await actor(browser,'emp07');const shenzhen=await actor(browser,'hr02');const guangzhou=await actor(browser,'hr01');
 const title='拟真R05｜实习入职前：请深圳HR确认报到楼层、时间和联系人';let ca:any;
 try{
  await diary.step('实习生 emp07','入职前整理通知（情境设定）','用自然语言询问报到信息，补充系统缺少的城市和日期',async()=>{
   const answer=await ask(employee.page,'我实习入职应该去哪报到？通知还没看明白。');
   expect(answer).toMatch(/城市|计划入职/);await expect(employee.page.locator('.answer-title')).toContainText('还需要确认');
   await supplyMissingContext(employee.page);await expect(employee.page.locator('.context-line')).toContainText('深圳');await expect(employee.page.locator('.context-line')).toContainText('实习生');
   await expect(employee.page.locator('.answer-card .citations')).not.toBeEmpty();
   await diary.photo(employee.page,'clarified-context');
   return `员工自报深圳及${futureDate(12)}，雇佣类型仍取档案中的实习生；离线回答显示适用条件与引用。档案欢迎区仍显示待完善，自报条件出现在回答区域。`;
  });
  await diary.step('实习生 emp07','需要人工确认行程','创建入职咨询并查看主责团队',async()=>{
   ca=await createCase(employee.page,title,'GENERAL_ONBOARDING');
   expect(ca.assigned_group).toBe('深圳HR');expect(ca.assigned_to).toBe('hr02');
   await openCase(employee.page,title);await expect(employee.page.locator('.detail-owner')).toContainText('深圳HR');
   return `创建${ca.case_number}，自报城市参与路由，未因原档案城市为空误派广州HR。`;
  });
  await diary.step('广州HR hr01 与 深圳HR hr02','两个城市独立登录','验证跨城市不可见，并由深圳HR接单公开解释',async()=>{
   await guangzhou.page.reload();await expect(guangzhou.page.getByRole('button',{name:title,exact:true})).toHaveCount(0);
   const denied=await guangzhou.page.request.get('/api/cases/'+ca.id);expect(denied.status()).toBe(404);
   await openCase(shenzhen.page,title);await accept(shenzhen.page);
   await publicReply(shenzhen.page,'已按你自报的深圳和计划入职日核对。请按本请求所引用的深圳报到制度准备，我会把楼层与联系人确认给你。');
   await expect(shenzhen.page.getByRole('button',{name:/修改员工档案|修改计划入职日|编辑员工资料/})).toHaveCount(0);
   await diary.photo(shenzhen.page,'hr-owner');
   return '深圳HR可接单、广州HR列表不可见且只读直接访问被拒绝。HR页面未提供员工档案更正入口，不能在该UI中验证已存档案纠错闭环。';
  });
  await diary.step('深圳HR hr02 → 实习生 emp07','人工解释后交付','给出带出处的报到说明，由员工阅读并确认',async()=>{
   await shenzhen.page.getByLabel('员工可见回复',{exact:true}).fill('已核对本请求的深圳报到制度原文：计划入职日10:00到深圳演示园区B座3楼，联系人为深圳HR演示服务台。此为模拟安排，请确认本次报到咨询是否已解决。');
   await shenzhen.page.getByRole('button',{name:'处理完毕，交员工确认',exact:true}).click();await expect(shenzhen.page.locator('.detail-summary')).toContainText('待员工确认');
   await refreshCase(employee.page);await expect(employee.page.locator('.messages')).toContainText('深圳HR演示服务台');
   await employee.page.getByRole('button',{name:'确认处理结果',exact:true}).click();await expect(employee.page.locator('.detail-summary')).toContainText('已确认完成');
   const closed=await readCase(employee.page,ca.id);expect(closed.status).toBe('closed');expect(closed.resolution_code).toBe('answered');await expect(employee.page.getByRole('dialog')).not.toContainText('补交安排已获批');await diary.photo(employee.page,'confirmed');
   return '请求经过待接单→处理中→待员工确认→已确认完成；这是模拟员工完成操作，不代表真实员工对该流程的评价。';
  });
 }finally{await Promise.all([employee.context.close(),shenzhen.context.close(),guangzhou.context.close()]);}
});

test('R06 HR拟定未来报到版本，管理员处理日期重叠，员工保留旧案依据',async({browser})=>{
 const diary=new Diary('R06');const employee=await actor(browser,'emp07');const author=await actor(browser,'hr02');const admin=await actor(browser,'admin01');
 const title='拟真R06｜预先确认深圳报到安排，保留当时制度依据';const version='1.1-roleplay';let ca:any;let draft:any;
 try{
  await diary.step('实习生 emp07','制度变更前提出咨询','查看旧报到说明，创建需保留原始依据的请求',async()=>{
   await ask(employee.page,'深圳报到地点和联系人是什么？');await supplyMissingContext(employee.page);
   await expect(employee.page.locator('.answer-text')).toContainText('3 楼');
   ca=await createCase(employee.page,title,'GENERAL_ONBOARDING');
   expect(ca.policy_snapshot_refs.some((r:any)=>r.article_version_id==='pol-report-sz-v1')).toBe(true);
   await openCase(employee.page,title);await diary.photo(employee.page,'original-case');
   return `${ca.case_number} 创建时保留report-sz v1.0引用，员工计划入职日${futureDate(12)}。`;
  });
  await diary.step('深圳HR hr02','准备一次临时楼层调整','创建v1.0后续草稿，先遗漏结束日期以还原实际编写失误',async()=>{
   await knowledge(author.page);await policyRow(author.page,'1.0').getByRole('button',{name:'创建后续版本',exact:true}).click();
   await author.page.getByLabel('版本',{exact:true}).fill(version);
   await author.page.getByLabel('生效日期（含）',{exact:true}).fill(futureDate(2));
   await author.page.getByLabel('正文（Markdown）').fill('仅用于作品演示，不代表任何真实企业制度。\n\n## 深圳报到地点与联系人\n请于计划入职日10:00到深圳演示园区B座4楼报到，联系人为深圳HR演示服务台。本临时安排于2027年1月1日前适用。');
   draft=await savePolicy(author.page);await expect(policyRow(author.page,version)).toContainText('草稿');
   await diary.photo(author.page,'author-draft');return `HR保存后续草稿${draft.id}，开始日期为${futureDate(2)}，留空结束日期。尚未自动发布。`;
  });
  await diary.step('管理员 admin01','审核日常制度发布','尝试发布并识别与既有2027版日期重叠，保留草稿返修',async()=>{
   await knowledge(admin.page);const publication=admin.page.waitForResponse(r=>r.url().endsWith('/publish')&&r.request().method()==='POST');
   await policyRow(admin.page,version).getByRole('button',{name:'审核发布',exact:true}).click();expect((await publication).status()).toBe(409);
   await expect(admin.page.getByRole('alert')).toContainText('范围存在交集');await expect(policyRow(admin.page,version)).toContainText('草稿');
   const policies=await (await admin.page.request.get('/api/admin/knowledge')).json();expect(policies.find((p:any)=>p.id==='pol-report-sz-v1').effective_to).toBe('2027-01-01');
   await diary.photo(admin.page,'overlap-rejected');await admin.page.getByRole('button',{name:'关闭错误',exact:true}).click();
   return '重叠发布返回409；草稿仍可编辑，原v1.0结束日期未被失败发布改写。未自动越过管理员审核。';
  });
  await diary.step('深圳HR hr02 → 管理员 admin01','原作者纠正后重新审核','补上不含结束日2027-01-01，再由不同身份发布',async()=>{
   await author.page.reload();await author.page.getByLabel('搜索制度').fill('report-sz');await policyRow(author.page,version).getByRole('button',{name:'编辑',exact:true}).click();
   await author.page.getByLabel('结束日期（不含，可空）',{exact:true}).fill('2027-01-01');await savePolicy(author.page);
   await admin.page.reload();await admin.page.getByLabel('搜索制度').fill('report-sz');
   const publication=admin.page.waitForResponse(r=>r.url().endsWith('/publish')&&r.request().method()==='POST');
   await policyRow(admin.page,version).getByRole('button',{name:'审核发布',exact:true}).click();expect((await publication).status()).toBe(200);await expect(policyRow(admin.page,version)).toContainText('已发布');
   const policies=await (await admin.page.request.get('/api/admin/knowledge')).json();const published=policies.find((p:any)=>p.version===version);
   expect(published.owner_id).toBe('hr02');expect(published.approved_by).toBe('admin01');expect(policies.find((p:any)=>p.id==='pol-report-sz-v1').effective_to).toBe(futureDate(2));
   await diary.photo(admin.page,'published');return '原v1.0结束于新版本开始日，临时v1.1结束于2027版开始日。作者和发布审核人不同，日期边界无重叠。';
  });
  await diary.step('实习生 emp07','再次查最新安排并回看旧请求','当前回答匹配新版本，同时旧请求打开原版本全文',async()=>{
   await closeDialog(employee.page);await ask(employee.page,'深圳报到地点和联系人是什么？');
   await expect(employee.page.locator('.answer-card .citations')).toContainText(version);await expect(employee.page.locator('.answer-text')).toContainText('4楼');
   await diary.photo(employee.page,'current-citation');await openCase(employee.page,title);
   const caseDialog=employee.page.getByRole('dialog',{name:ca.case_number+' · 入职咨询',exact:true});
   const original=caseDialog.locator('.citations button').filter({hasText:'深圳报到地点与联系人'});await original.click();
   const citation=employee.page.getByRole('dialog',{name:'制度原文与引用版本',exact:true});
   await expect(citation).toBeVisible();await expect(citation).toContainText('v1.0');await expect(citation).toContainText('3 楼');
   await expect(employee.page.getByRole('dialog').last()).toHaveAttribute('aria-label','制度原文与引用版本');
   await citation.getByRole('button',{name:'关闭',exact:true}).click({trial:true});
   await expect(citation.getByRole('button',{name:'关闭',exact:true})).toBeFocused();
   const old=await readCase(employee.page,ca.id);expect(old.policy_snapshot_refs.some((r:any)=>r.article_version_id==='pol-report-sz-v1')).toBe(true);
   await diary.photo(employee.page,'historical-citation');
   await employee.page.keyboard.press('Escape');await expect(citation).toHaveCount(0);await expect(caseDialog).toBeVisible();await expect(employee.page.getByRole('dialog')).toHaveCount(1);await expect(original).toBeFocused();
   await diary.photo(employee.page,'citation-closed-case-preserved');
   return '同一员工的新咨询引用v1.1-roleplay与4楼；旧请求顶层原文窗口显示v1.0与3楼，关闭按钮可操作；Esc只关闭原文、保留原工单并恢复引用按钮焦点。原办理依据未被改写。';
  });
 }finally{await Promise.all([employee.context.close(),author.context.close(),admin.context.close()]);}
});

test('R07 管理员从首次缺项与低分评价追溯真实测试记录，不把重试算完整',async({browser})=>{
 const diary=new Diary('R07');const employee=await actor(browser,'emp06');const hr=await actor(browser,'hr02');const admin=await actor(browser,'admin01');
 const title='拟真R07｜赶时间漏填说明后补全：请HR确认深圳报到联系人';let draftId='';let ca:any;let before:any;
 try{
  await diary.step('管理员 admin01','晨间服务复盘（情境设定）','明确选择自动测试数据，记录首次完整率基线',async()=>{
   await showAnalytics(admin.page);before=await summary(admin.page);
   await expect(admin.page.locator('.metric-card').filter({hasText:'一次提交完整率'})).toContainText('包含被缺项拦截');
   return `明确筛选test，基线首次完整率为${before.metrics.first_submission_completeness.numerator}/${before.metrics.first_submission_completeness.denominator}。此数值仅来自自动测试操作，不能当作企业业务表现。`;
  });
  await diary.step('员工 emp06','通勤途中赶着提交（情境设定）','漏填事项说明仍点确认，阅读缺项提示后在同一草稿补填重试',async()=>{
   await employee.page.getByRole('button',{name:'新建服务请求',exact:true}).click();
   const preview=employee.page.waitForResponse(r=>r.url().endsWith('/api/cases/draft')&&r.request().method()==='POST');await employee.page.getByRole('button',{name:'预览请求',exact:true}).click();const draft=await (await preview).json();draftId=draft.id;await expect(employee.page.getByRole('button',{name:'确认提交',exact:true})).toBeVisible();
   const duplicate=employee.page.getByLabel('本次为新的独立事项，仍需另建请求');if(await duplicate.isVisible())await duplicate.check();
   const failed=employee.page.waitForResponse(r=>r.url().endsWith('/api/cases')&&r.request().method()==='POST');await employee.page.getByRole('button',{name:'确认提交',exact:true}).click();expect((await failed).status()).toBe(422);
   await expect(employee.page.getByRole('alert')).toContainText('事项说明');await diary.photo(employee.page,'incomplete-first-submit');
   await employee.page.getByLabel('事项说明',{exact:true}).fill(title);await employee.page.getByRole('button',{name:'预览请求',exact:true}).click();await expect(employee.page.getByRole('button',{name:'确认提交',exact:true})).toBeVisible();if(await duplicate.isVisible())await duplicate.check();
   const submitted=employee.page.waitForResponse(r=>r.url().endsWith('/api/cases')&&r.request().method()==='POST');await employee.page.getByRole('button',{name:'确认提交',exact:true}).click();const r=await submitted;expect(r.status()).toBe(200);ca=await r.json();await expect(employee.page.getByRole('dialog')).toHaveCount(0);
   const after=await summary(admin.page);expect(after.metrics.first_submission_completeness.denominator).toBe(before.metrics.first_submission_completeness.denominator+1);expect(after.metrics.first_submission_completeness.numerator).toBe(before.metrics.first_submission_completeness.numerator);
   const rows=await detail(admin.page,'first_submission_completeness');const row=rows.rows.find((r:any)=>r.draft_id===draftId);expect(row.validation_result).toBe('invalid');expect(row.attempt_no).toBe(1);
   return `草稿${draftId}首次422被拦截，修正后生成${ca.case_number}。分母只增加1、分子未增加，最终成功没有覆盖首次缺项。`;
  });
  await diary.step('深圳HR hr02 → 员工 emp06','收到解释但仍觉得不清楚','HR提交结果，员工如实选择困难与未解决，而不是被迫打高分',async()=>{
   await openCase(hr.page,title);await accept(hr.page);await hr.page.getByLabel('员工可见回复',{exact:true}).fill('已核对深圳报到制度，联系人为深圳HR演示服务台。请按当前适用制度中的安排报到，如仍有问题请直接说明。');
   await hr.page.getByRole('button',{name:'处理完毕，交员工确认',exact:true}).click();await expect(hr.page.locator('.detail-summary')).toContainText('待员工确认');
   await openCase(employee.page,title);await employee.page.getByLabel('容易程度').selectOption('1');await employee.page.getByLabel('问题解决情况').selectOption('unresolved');
   await employee.page.getByRole('button',{name:'提交反馈',exact:true}).click();await expect(employee.page.getByRole('dialog')).toContainText('感谢反馈');
   const resolved=await readCase(employee.page,ca.id);expect(resolved.status).toBe('resolved');expect(resolved.resolution_code).toBe('answered');await expect(employee.page.getByRole('dialog')).not.toContainText('补交安排已获批');await diary.photo(employee.page,'negative-feedback');
   return '以设定的困难/未解决情境提交1分评价，反馈未自动替员工确认完成。该分数为测试输入，不是真实满意度。';
  });
  await diary.step('管理员 admin01','寻找具体改进对象','从一次提交完整率追到缺项记录，再检查评价明细并复算',async()=>{
   await admin.page.reload();await admin.page.getByLabel('数据来源').selectOption('test');await expect(admin.page.locator('.metric-grid')).toBeVisible();
   await admin.page.locator('.metric-card').filter({hasText:'一次提交完整率'}).click();const attemptRow=admin.page.getByRole('dialog').locator('tbody tr').filter({hasText:draftId});await attemptRow.scrollIntoViewIfNeeded();await expect(attemptRow).toContainText('invalid');await diary.photo(admin.page,'first-attempt-evidence');await closeDialog(admin.page);
   await admin.page.locator('.metric-card').filter({hasText:'容易程度评分'}).click();const feedbackRow=admin.page.getByRole('dialog').locator('tbody tr').filter({hasText:ca.id});await feedbackRow.scrollIntoViewIfNeeded();await expect(feedbackRow).toContainText('unresolved');await diary.photo(admin.page,'feedback-evidence');await closeDialog(admin.page);
   const values=await summary(admin.page);const feedback=await detail(admin.page,'ease_score');const own=feedback.rows.find((r:any)=>r.case_id===ca.id);expect(own.ease).toBe(1);expect(own.outcome).toBe('unresolved');
   const expected=feedback.rows.reduce((sum:number,r:any)=>sum+r.ease,0)/feedback.rows.length;expect(values.metrics.ease_score.value).toBeCloseTo(expected,2);
   return `评价行包含本次请求、ease=1、outcome=unresolved，按${feedback.rows.length}条有效反馈独立求平均与指标一致。首次提交明细仍保留invalid原始记录；页面的明细字段部分为英文技术字段，运营理解成本单独记录。`;
  });
 }finally{await Promise.all([employee.context.close(),hr.context.close(),admin.context.close()]);}
});
