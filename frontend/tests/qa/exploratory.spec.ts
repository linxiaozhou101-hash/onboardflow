import {test,expect} from '@playwright/test';
import type {Page} from '@playwright/test';
import {writeFileSync} from 'node:fs';
async function login(page:Page,id:string){await page.goto('/');await page.locator('.account').filter({hasText:id}).click();await expect(page.locator('.sidebar')).toBeVisible();}
async function switchTo(page:Page,id:string){await page.getByRole('button',{name:'演示身份切换'}).click();await page.locator('.account').filter({hasText:id}).click();await expect(page.getByRole('dialog')).toHaveCount(0);}
async function newRequest(page:Page,title:string){await page.getByRole('button',{name:'新建服务请求'}).click();await page.getByLabel('事项说明').fill(title);await page.getByRole('button',{name:'预览请求',exact:true}).click();const duplicate=page.getByLabel('本次为新的独立事项，仍需另建请求');if(await duplicate.isVisible())await duplicate.check();}

test('QA-UI-01 real keyboard typing preserves focus and full text in modal',async({page})=>{
 await login(page,'emp01');await page.getByRole('button',{name:'新建服务请求'}).click();
 const input=page.getByLabel('事项说明');await input.click();await input.pressSequentially('Keyboard input must not lose focus',{delay:15});
 await expect(input).toHaveValue('Keyboard input must not lose focus');await expect(input).toBeFocused();
});
test('QA-UI-02 modal initially traps reverse tab and restores opener on Escape',async({page})=>{
 await login(page,'emp01');const opener=page.getByRole('button',{name:'新建服务请求'});await opener.click();
 await page.keyboard.press('Shift+Tab');expect(await page.evaluate(()=>!!document.activeElement?.closest('[role=dialog]'))).toBe(true);
 await page.keyboard.press('Escape');await expect(page.getByRole('dialog')).toHaveCount(0);await expect(opener).toBeFocused();
});
test('QA-UI-03 failed submission response retry creates only one case and preserves draft',async({page})=>{
 await login(page,'emp05');const title='QA retry response loss '+Date.now();await newRequest(page,title);
 let failures=0;let actualId='';
 await page.route('**/api/cases',async route=>{if(route.request().method()==='POST'&&failures++===0){const response=await route.fetch();actualId=(await response.json()).id;await route.abort('failed');}else await route.continue()});
 await page.getByRole('button',{name:'确认提交',exact:true}).click();await expect(page.locator('.form-error')).toBeVisible();await expect(page.getByLabel('事项说明')).toHaveValue(title);
 await page.getByRole('button',{name:'确认提交',exact:true}).click();await expect(page.getByRole('dialog')).toHaveCount(0);
 const rows=await (await page.request.get('/api/cases')).json();const matches=rows.filter((r:any)=>r.description===title);expect(matches).toHaveLength(1);expect(matches[0].id).toBe(actualId);
});
test('QA-UI-04 delayed HR01 list never replaces HR02 list after identity switch',async({page})=>{
 await login(page,'hr01');await expect(page.locator('tbody tr').first()).toBeVisible();
 let release:()=>void=()=>{};const gate=new Promise<void>(r=>release=r);let intercepted:()=>void=()=>{};const ready=new Promise<void>(r=>intercepted=r);
 let firstHeld=0;await page.route('**/api/cases?*',async route=>{if(route.request().url().includes('status=assigned')&&!firstHeld++){const response=await route.fetch();intercepted();await gate;await route.fulfill({response});}else await route.continue()});
 await page.getByLabel('状态').selectOption('assigned');await ready;
 const newResponse=page.waitForResponse(r=>r.url().includes('/api/cases?'));await switchTo(page,'hr02');await newResponse;await expect(page.locator('.user-block')).toContainText('深圳');release();await page.waitForTimeout(200);
 await expect(page.locator('main')).not.toContainText('广州HR');
});
test('QA-UI-05 delayed case detail cannot surface under a new employee session',async({page})=>{
 await login(page,'hr01');await expect(page.locator('tbody tr').first()).toBeVisible();
 let release:()=>void=()=>{};const gate=new Promise<void>(r=>release=r);let intercepted:()=>void=()=>{};const ready=new Promise<void>(r=>intercepted=r);
 await page.route(/\/api\/cases\/demo_case_\d+$/,async route=>{const response=await route.fetch();intercepted();await gate;await route.fulfill({response})});
 await page.locator('tbody tr').filter({hasText:'OF-DEMO'}).first().locator('button').click();await ready;await switchTo(page,'emp02');release();await page.waitForTimeout(200);
 await expect(page.getByRole('dialog')).toHaveCount(0);
});
test('QA-UI-06 rejected analytics filters must not keep previous metric results as current',async({page})=>{
 await login(page,'admin01');await page.getByRole('link',{name:'服务分析'}).click();await expect(page.locator('.metric-card').first()).toBeVisible();
 await page.getByLabel('开始日期').fill('2027-12-31');await expect(page.getByRole('alert')).toBeVisible();
 await expect(page.locator('.metric-card')).toHaveCount(0);
});
test('QA-UI-07 ordinary text contrast sampling is at least 4.5 to 1',async({page})=>{
 await login(page,'emp04');await expect(page.locator('.checklist-items')).toBeVisible();
 const results=await page.locator('.page-heading p,.welcome-strip dt,.checklist-items small,.subtle,.fineprint,.badge').evaluateAll(nodes=>{
 const rgb=(s:string)=>s.match(/[\d.]+/g)?.map(Number)||[255,255,255];const lum=(v:number[])=>v.slice(0,3).map(x=>{x/=255;return x<=.04045?x/12.92:Math.pow((x+.055)/1.055,2.4)}).reduce((a,x,i)=>a+x*[.2126,.7152,.0722][i],0);
 return nodes.filter(n=>(n as HTMLElement).offsetParent!==null).map(n=>{const c=getComputedStyle(n);let bg='';let p:Element|null=n;while(p){const s=getComputedStyle(p).backgroundColor;if(s!=='rgba(0, 0, 0, 0)'&&s!=='transparent'){bg=s;break}p=p.parentElement}const a=lum(rgb(c.color)),b=lum(rgb(bg||'rgb(255,255,255)'));return{text:n.textContent?.slice(0,60),selector:n.className,foreground:c.color,background:bg,fontSize:c.fontSize,ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05)}});
 });writeFileSync('../reports/qa_20261124/contrast.json',JSON.stringify(results,null,2));expect(results.filter(r=>r.ratio<4.5),JSON.stringify(results)).toEqual([]);
});
test('QA-UI-08 mobile 320 and 390 allow horizontal table scrolling without body overflow',async({page})=>{
 await login(page,'emp02');for(const width of [320,390]){await page.setViewportSize({width,height:844});await expect(page.locator('tbody tr').first()).toBeVisible();expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);const table=page.locator('.table-wrap');expect(await table.evaluate(e=>getComputedStyle(e).overflowX)).toMatch(/auto|scroll/);await table.evaluate(e=>{e.scrollLeft=e.scrollWidth});expect(await table.evaluate(e=>e.scrollLeft)).toBeGreaterThan(0);await page.screenshot({path:`../reports/qa_20261124/screenshots/mobile-${width}.png`,fullPage:true})}
});
test('QA-UI-09 independent HR author/admin publish and new policy is searchable',async({page})=>{
 await login(page,'hr01');await page.getByRole('link',{name:'制度知识库'}).click();await page.getByRole('button',{name:'新建制度草稿'}).click();
 const key='qa-publish-'+Date.now();await page.getByLabel('制度编号').fill(key);await page.getByLabel('制度名称').fill('创意工作坊报到说明');await page.getByLabel('生效日期（含）').fill('2026-11-01');await page.getByLabel('正文（Markdown）').fill('仅用于作品演示，不代表任何真实企业制度。\n\n## 创意工作坊\n新同事参加创意工作坊，请到紫竹楼七层。');await page.getByRole('button',{name:'保存草稿'}).click();await expect(page.getByRole('dialog')).toHaveCount(0);
 const authorRow=page.locator('tr').filter({hasText:key});await expect(authorRow).toContainText('草稿');await expect(authorRow.getByRole('button',{name:'审核发布'})).toHaveCount(0);
 await switchTo(page,'admin01');await page.getByRole('link',{name:'制度知识库'}).click();await page.locator('tr').filter({hasText:key}).getByRole('button',{name:'审核发布'}).click();await expect(page.locator('tr').filter({hasText:key})).toContainText('已发布');
 await switchTo(page,'emp01');await page.getByLabel('输入入职问题').fill('创意工作坊到哪里报到？');await page.getByRole('button',{name:'发送',exact:true}).click();await expect(page.locator('.answer-text')).toContainText('紫竹楼七层');await expect(page.locator('.answer-card .citations')).toContainText('创意工作坊报到说明');
});
test('QA-UI-10 expired case row detects conflict and allows refresh without losing response',async({page})=>{
 await login(page,'hr01');const all=await(await page.request.get('/api/cases?status=assigned')).json();const ca=all[0];await page.getByRole('button',{name:ca.description,exact:true}).first().click();await expect(page.getByRole('dialog')).toBeVisible();
 const me=await(await page.request.get('/api/me')).json();expect((await page.request.post(`/api/cases/${ca.id}/actions/accept`,{headers:{'X-CSRF-Token':me.csrf_token},data:{row_version:ca.row_version}})).ok()).toBe(true);
 await page.getByLabel('员工可见回复').fill('并发更新期间新输入的回复');await page.getByRole('button',{name:'接单并开始处理'}).click();await expect(page.getByRole('alert')).toContainText('刷新');
 await expect(page.getByLabel('员工可见回复')).toHaveValue('并发更新期间新输入的回复');await page.getByRole('button',{name:'刷新请求',exact:true}).click();await expect(page.locator('.detail-summary')).toContainText('处理中');await expect(page.getByLabel('员工可见回复')).toHaveValue('并发更新期间新输入的回复');
});
test('QA-UI-11 knowledge editor supports typing without per-keystroke focus reset',async({page})=>{
 await login(page,'hr01');await page.getByRole('link',{name:'制度知识库'}).click();await page.getByRole('button',{name:'新建制度草稿'}).click();
 const input=page.getByLabel('制度名称');await input.click();await input.pressSequentially('Policy editor keyboard',{delay:10});await expect(input).toHaveValue('Policy editor keyboard');await expect(input).toBeFocused();
});
test('QA-UI-12 changed policy context requires new preview without losing fields',async({page})=>{
 await login(page,'emp01');const title='QA preserve context correction';await newRequest(page,title);
 await page.route('**/api/cases',async route=>{if(route.request().method()==='POST')await route.fulfill({status:409,json:{detail:{code:'context_changed',message:'查询条件已变化，请重新预览'}}});else await route.continue()});
 await page.getByRole('button',{name:'确认提交',exact:true}).click();await expect(page.getByRole('button',{name:'预览请求',exact:true})).toBeVisible();await expect(page.getByLabel('事项说明')).toHaveValue(title);
 await page.unroute('**/api/cases');await page.getByRole('button',{name:'预览请求',exact:true}).click();await expect(page.getByRole('button',{name:'确认提交',exact:true})).toBeVisible();
});
test('QA-UI-13 stored user text is inert and survives browser reload',async({page})=>{
 await login(page,'emp01');const text='<img src=x onerror="window.__qa_xss=1"> QA literal text';await newRequest(page,text);
 await page.getByRole('button',{name:'确认提交',exact:true}).click();await expect(page.getByRole('dialog')).toHaveCount(0);await page.reload();await expect(page.getByRole('button',{name:text,exact:true})).toBeVisible();
 expect(await page.evaluate(()=> (window as any).__qa_xss)).toBeUndefined();await page.getByRole('button',{name:text,exact:true}).click();await expect(page.locator('.detail-summary')).toContainText(text);expect(await page.locator('.detail-summary img').count()).toBe(0);
});
test('QA-UI-14 calendar filter and CSV export use Shanghai midnight with explicit exclusive end',async({page})=>{
 await login(page,'admin01');await page.getByRole('link',{name:'服务分析'}).click();await expect(page.locator('.metric-card').first()).toBeVisible();
 const request=page.waitForRequest(r=>r.url().includes('/api/analytics/summary?')&&r.url().includes('start='));await page.getByLabel('开始日期').fill('2026-11-01');
 expect(new URL((await request).url()).searchParams.get('start')).toBe('2026-11-01T00:00:00+08:00');
 await page.getByLabel('结束日期（不含）',{exact:true}).fill('2026-11-02');await expect(page.locator('.metric-card').first()).toBeVisible();
 const href=await page.getByRole('link',{name:'导出记录'}).getAttribute('href');const url=new URL(href!,'http://127.0.0.1:8003');expect(url.searchParams.get('end')).toBe('2026-11-02T00:00:00+08:00');expect(url.searchParams.get('start')).toBe('2026-11-01T00:00:00+08:00');
 const response=await page.request.get(url.href);expect(response.status()).toBe(200);expect(response.headers()['content-type']).toContain('text/csv');
});
