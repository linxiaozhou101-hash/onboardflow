import {test,expect} from '@playwright/test';
import type {Page} from '@playwright/test';
import {Diary,actor,openCase,refreshCase,accept,createCase,readCase,publicReply,closeDialog,ask} from './helpers';

async function createTask(hr:Page,caseId:string,type:string,assignee:string,dependencies:string[]=[],instruction=''){
  await hr.getByLabel('任务类型').selectOption(type);
  await hr.getByLabel('IT 负责人').selectOption(assignee);
  await hr.getByLabel('办结前必须完成').check();
  await hr.getByLabel('IT 任务说明').fill(instruction);
  const deps=hr.getByLabel('前置任务（可多选）');
  if(await deps.count())await deps.selectOption(dependencies);
  const response=hr.waitForResponse(r=>r.url().endsWith('/actions/create_task')&&r.request().method()==='POST');
  await hr.getByRole('button',{name:'创建协同任务',exact:true}).click();
  const received=await response;expect(received.status()).toBe(200);
  const current=await received.json();
  await expect(hr.locator('.task-row')).toHaveCount(current.tasks.length);
  const prior=await readCase(hr,caseId);
  return prior.tasks[prior.tasks.length-1];
}
async function taskCard(page:Page,id:string){
  await page.getByRole('button',{name:'刷新',exact:true}).click();
  const card=page.locator(`[data-task-id="${id}"]`);await expect(card).toBeVisible();return card;
}
async function finishTask(page:Page,id:string){
  const card=await taskCard(page,id);await card.getByRole('button',{name:'完成演示任务',exact:true}).click();
  await expect(card).toContainText('演示任务已完成');return card;
}

test('R03 深圳账号与电脑双团队交接，员工发现结果不符后重开并完成修正',async({browser})=>{
  const diary=new Diary('R03');
  const emp=await actor(browser,'emp02');const hr=await actor(browser,'hr02');
  const accountIT=await actor(browser,'it01');const deviceIT=await actor(browser,'it02');
  const contexts=[emp.context,hr.context,accountIT.context,deviceIT.context];
  const title='拟真R03｜深圳首日：办公账号和Windows演示电脑都未准备，项目培训即将开始';
  const internal='HR内部：已与培训负责人商量缓冲时间，不展示给IT或员工。';
  const accountInstruction='准备培训演示账号，不开通真实账号。';
  const deviceInstruction='准备Windows演示电脑供培训展示，账号任务完成后核对；仅模拟执行。';
  const correctionInstruction='核对Windows演示电脑的培训展示安排，补录确认结果；仅模拟执行。';
  let ca:any;let account:any;let device:any;let correction:any;
  try{
    await diary.step('员工 emp02','入职第一天上午（情境设定）','查看当前账号进度，再明确创建新的培训设备事项',async()=>{
      const text=await ask(emp.page,'我今天的账号和设备准备到哪一步了？');
      expect(text).toContain('已有');
      ca=await createCase(emp.page,title,'ACCESS_DEVICE',{device_type:'both'});
      expect(ca.assigned_group).toBe('深圳HR');expect(ca.assigned_to).toBe('hr02');
      await openCase(emp.page,title);await expect(emp.page.locator('.detail-summary')).toContainText('HR 将接单');
      await diary.photo(emp.page,'employee-new-request');
      return `系统先展示已有账号请求；员工确认本次是新事项，建立${ca.case_number}，状态${ca.status}，主责${ca.assigned_group}/${ca.assigned_to}。`;
    });
    await diary.step('深圳HR hr02','收到员工紧急请求','接单、解释安排，并分开保存内部协调说明',async()=>{
      await openCase(hr.page,title);await accept(hr.page);
      await publicReply(hr.page,'已接单。我会先安排办公账号，再安排Windows演示电脑；两个演示任务都完成后请你核对，培训时间由HR协调。');
      await hr.page.getByLabel('HR 内部备注（员工不可见）').fill(internal);
      await hr.page.getByRole('button',{name:'保存内部备注',exact:true}).click();await expect(hr.page.locator('.message.internal')).toContainText(internal);
      await refreshCase(emp.page);await expect(emp.page.locator('.messages')).toContainText('先安排办公账号');
      await expect(emp.page.getByRole('dialog')).not.toContainText(internal);
      return '员工看到处理顺序和HR说明；内部培训协调备注仍只在HR视图出现。';
    });
    await diary.step('深圳HR hr02','需要跨两位IT协同','账号任务派it01，电脑任务派it02并设为依赖账号',async()=>{
      account=await createTask(hr.page,ca.id,'IT_ACCESS','it01',[],accountInstruction);
      device=await createTask(hr.page,ca.id,'IT_DEVICE','it02',[account.id],deviceInstruction);
      expect(device.dependency_task_ids).toContain(account.id);expect(account.required).toBe(true);expect(device.required).toBe(true);
      expect(account.minimal_context.instruction).toBe(accountInstruction);expect(device.minimal_context.instruction).toBe(deviceInstruction);
      const state=await readCase(hr.page,ca.id);expect(state.assigned_to).toBe('hr02');
      await diary.photo(hr.page,'hr-dependencies');
      return `两个必需任务分别交给it01与it02；HR分别填写不含内部协调备注的IT说明，主工单仍由hr02负责，电脑前置依赖${account.id}。`;
    });
    await diary.step('设备IT it02','先看到自己的任务','尝试在账号尚未完成时先完成电脑任务，确认拦截及信息边界',async()=>{
      const card=await taskCard(deviceIT.page,device.id);
      await expect(card).toContainText('深圳');await expect(card).toContainText('演示员工02');
      await expect(deviceIT.page.locator(`[data-task-id="${account.id}"]`)).toHaveCount(0);
      await expect(deviceIT.page.locator('main')).not.toContainText(internal);
      const visibleInstruction=await card.locator('dl > div').filter({hasText:'任务说明'}).innerText();
      await expect(card).toContainText(deviceInstruction);
      await card.getByRole('button',{name:'完成演示任务',exact:true}).click();
      await expect(deviceIT.page.getByRole('alert')).toContainText('前置任务');
      expect((await readCase(hr.page,ca.id)).tasks.find((t:any)=>t.id===device.id).status).toBe('pending');
      await diary.photo(deviceIT.page,'device-blocked');
      await deviceIT.page.getByRole('button',{name:'关闭错误',exact:true}).click();
      return `只看到本人任务的员工姓名、工号、城市、计划日期和任务说明，未见HR内部备注；前置任务未完成被拦截。页面实读说明：${visibleInstruction}。本轮已验证Windows要求可从HR专门填写的说明传达到IT。`;
    });
    await diary.step('账号IT it01 → 设备IT it02','前置依赖交接','先完成账号演示任务，再由另一账号完成电脑演示任务',async()=>{
      const card=await taskCard(accountIT.page,account.id);await expect(card).toContainText(accountInstruction);await expect(accountIT.page.locator('main')).not.toContainText(internal);
      await finishTask(accountIT.page,account.id);
      await expect(accountIT.page.locator(`[data-task-id="${device.id}"]`)).toHaveCount(0);
      await finishTask(deviceIT.page,device.id);
      const state=await readCase(hr.page,ca.id);expect(state.tasks.every((t:any)=>t.status==='completed')).toBe(true);expect(state.status).toBe('in_progress');
      await diary.photo(deviceIT.page,'device-completed');
      return 'IT完成只改变子任务，主工单仍处理中，HR仍须核对并提交员工可见结果。';
    });
    await diary.step('深圳HR hr02','两个演示任务已完成','核对依赖后明确交付结果',async()=>{
      await refreshCase(hr.page);
      await hr.page.getByLabel('员工可见回复',{exact:true}).fill('两个演示任务已完成：办公账号及电脑准备流程已记录，请核对是否满足培训要求；这不是实际账号开通。');
      await hr.page.getByRole('button',{name:'处理完毕，交员工确认',exact:true}).click();
      await expect(hr.page.locator('.detail-summary')).toContainText('待员工确认');
      const state=await readCase(hr.page,ca.id);expect(state.status).toBe('resolved');
      await diary.photo(hr.page,'hr-delivery');
      // A device result must not announce deferred material approval.
      expect.soft(state.resolution_code,'账号设备事项不应带补交审批代码').not.toBe('approved_deferred');
      expect.soft(state.resolution_code,'账号设备事项不应带材料拒绝审批代码').not.toBe('rejected');
      await expect.soft(hr.page.getByRole('dialog'),'账号设备事项不应显示材料延期获批提示').not.toContainText('补交安排已获批');
      return `主单状态${state.status}；处理结论代码实际为${state.resolution_code}。专项断言检查是否错误带入材料审批提示。`;
    });
    await diary.step('员工 emp02','核对结果发现仍不符合预期','暂不确认，说明演示电脑缺少培训展示安排并申请重开',async()=>{
      await refreshCase(emp.page);await expect(emp.page.locator('.detail-summary')).toContainText('待员工确认');
      await emp.page.getByLabel('重开原因',{exact:true}).fill('已看到处理结果，但培训演示电脑安排仍未按需确认，请补充核对后再让我确认。');
      await emp.page.getByRole('button',{name:'申请重新处理',exact:true}).click();await expect(emp.page.locator('.detail-summary')).toContainText('处理中');
      const state=await readCase(emp.page,ca.id);expect(state.reopen_count).toBe(1);expect(state.tasks.every((t:any)=>t.status==='completed')).toBe(true);expect(state.due_at).toBe(ca.due_at);
      await diary.photo(emp.page,'employee-reopen');
      return '员工能够重开并解释原因；原两个已完成任务保留，SLA到期时间未被重置。';
    });
    await diary.step('深圳HR hr02 → 设备IT it02','收到员工纠正','HR明确新增修正任务，设备IT完成后再次交付',async()=>{
      await refreshCase(hr.page);
      await publicReply(hr.page,'已收到核对意见，原任务历史保留；我会新增一次设备核对演示任务。');
      correction=await createTask(hr.page,ca.id,'IT_DEVICE','it02',[],correctionInstruction);
      const pendingCorrection=await taskCard(deviceIT.page,correction.id);
      await expect(pendingCorrection).toContainText('待处理');
      await expect(pendingCorrection).toContainText(correctionInstruction);await expect(pendingCorrection).not.toContainText(internal);
      await expect(deviceIT.page.locator(`[data-task-id="${device.id}"]`)).toContainText('已完成');
      await diary.photo(deviceIT.page,'device-pending-and-completed-queue');
      await finishTask(deviceIT.page,correction.id);
      await refreshCase(hr.page);
      await hr.page.getByLabel('员工可见回复',{exact:true}).fill('新增设备核对演示任务已完成，原记录均已保留，请再次核对并确认本次处理结果。');
      await hr.page.getByRole('button',{name:'处理完毕，交员工确认',exact:true}).click();await expect(hr.page.locator('.detail-summary')).toContainText('待员工确认');
      return '重开未抹除旧任务，通过明确新增第三个任务记录修正工作。设备IT同一页面同时看见旧设备任务“已完成”与新修正任务“待处理”，可通过卡片状态区分，已截图保留。';
    });
    await diary.step('员工 emp02','再次核对后','确认结果，提交模拟反馈并刷新页面验证留存',async()=>{
      await refreshCase(emp.page);await emp.page.getByRole('button',{name:'确认处理结果',exact:true}).click();await expect(emp.page.locator('.detail-summary')).toContainText('已确认完成');
      await emp.page.getByRole('dialog').getByLabel('容易程度').selectOption('3');await emp.page.getByRole('dialog').getByLabel('问题解决情况').selectOption('resolved');await emp.page.getByRole('dialog').getByRole('button',{name:'提交反馈',exact:true}).click();await expect(emp.page.getByRole('dialog')).toContainText('感谢反馈');
      await openCase(emp.page,title);const state=await readCase(emp.page,ca.id);expect(state.status).toBe('closed');expect(state.tasks).toHaveLength(3);expect(state.reopen_count).toBe(1);expect(state.assigned_to).toBe('hr02');
      await diary.photo(emp.page,'employee-final-confirmation');
      return `最终${state.status}，3个任务全部完成，重开1次，HR主责未变。3分是预设模拟输入，不是实际人类易用性评分。`;
    });
  }finally{diary.save();for(const c of contexts)await c.close();}
});

test('R04 深圳临时设备请求取消，已完成账号留档、未完成电脑任务同步终止',async({browser})=>{
  const diary=new Diary('R04');
  const emp=await actor(browser,'emp06');const hr=await actor(browser,'hr02');const accountIT=await actor(browser,'it01');const deviceIT=await actor(browser,'it02');
  const contexts=[emp.context,hr.context,accountIT.context,deviceIT.context];
  const title='拟真R04｜深圳备用培训场地需要临时账号和演示电脑，后续安排可能调整';
  let ca:any;let account:any;let device:any;
  try{
    await diary.step('员工 emp06 → 深圳HR hr02','提前准备备用培训方案','提交账号设备请求，HR接单并安排两个任务',async()=>{
      ca=await createCase(emp.page,title,'ACCESS_DEVICE',{device_type:'both'});
      await openCase(hr.page,title);await accept(hr.page);
      await publicReply(hr.page,'先安排临时培训的两个演示任务；如果场地调整，请在原请求说明，避免重复准备。');
      account=await createTask(hr.page,ca.id,'IT_ACCESS','it01',[],'为备用培训方案准备演示账号；仅模拟，不开通真实账号。');device=await createTask(hr.page,ca.id,'IT_DEVICE','it02',[account.id],'为备用培训场地准备Windows演示电脑，先等待账号任务完成；仅模拟执行。');
      return `请求${ca.case_number}归深圳HR，两个必需任务已建立，等待账号准备。`;
    });
    await diary.step('账号IT it01','收到准备任务','先完成账号演示任务，电脑仍待准备',async()=>{
      await finishTask(accountIT.page,account.id);
      const state=await readCase(hr.page,ca.id);expect(state.tasks.find((t:any)=>t.id===account.id).status).toBe('completed');expect(state.tasks.find((t:any)=>t.id===device.id).status).toBe('pending');
      return '账号任务完成，电脑任务待办；主单仍由HR跟进，不自动办结。';
    });
    await diary.step('员工 emp06','培训安排改变（情境设定）','在原请求说明撤销原因并取消',async()=>{
      await openCase(emp.page,title);
      await emp.page.locator('.cancel-section summary').click();await emp.page.getByLabel('取消原因',{exact:true}).fill('培训改在已有设备的会议室，不再需要备用账号设备准备；请停止未完成的准备工作。');
      await emp.page.getByRole('button',{name:'确认取消请求',exact:true}).click();await expect(emp.page.locator('.detail-summary')).toContainText('已取消');
      const state=await readCase(emp.page,ca.id);expect(state.status).toBe('cancelled');expect(state.cancelled_at).toBeTruthy();expect(state.latest_resolved_at).toBeNull();expect(state.assigned_to).toBe('hr02');
      expect(state.tasks.find((t:any)=>t.id===account.id).status).toBe('completed');expect(state.tasks.find((t:any)=>t.id===device.id).status).toBe('cancelled');
      await expect(emp.page.getByRole('button',{name:'申请重新处理',exact:true})).toHaveCount(0);
      await diary.photo(emp.page,'employee-cancelled');
      return '取消原因留在时间线；账号已完成历史保留，电脑未完成任务同时取消，主单没有被计为解决。';
    });
    await diary.step('设备IT it02 → 深圳HR hr02','准备开始设备工作前','刷新自己的待办和HR原请求，确认已终止',async()=>{
      const card=await taskCard(deviceIT.page,device.id);await expect(card).toContainText('已取消');await expect(card.getByRole('button',{name:'完成演示任务',exact:true})).toHaveCount(0);
      await refreshCase(hr.page);await expect(hr.page.locator('.detail-summary')).toContainText('已取消');await expect(hr.page.getByRole('button',{name:'创建协同任务',exact:true})).toHaveCount(0);
      await diary.photo(deviceIT.page,'device-cancelled');
      return 'IT刷新后可见取消状态，不能再完成被取消任务；HR也不能继续新增任务。页面未实时推送，离开再回来需要手动刷新（观察，不伪装自动通知）。';
    });
  }finally{diary.save();for(const c of contexts)await c.close();}
});
