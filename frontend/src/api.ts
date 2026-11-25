export type Row = Record<string, any>;
let csrf = '';
let identityEpoch = 0;
export const isStaleIdentity = (error: unknown) => error instanceof ApiError && error.code === 'stale_identity';
export const setCsrf = (token: string) => { if (csrf !== token) identityEpoch++; csrf = token; };
export class ApiError extends Error { code: string; detail: Row; constructor(detail: Row) { super(detail.message || detail.code || '操作未完成，请重试'); this.code = detail.code; this.detail = detail; } }
export async function api(path: string, method = 'GET', body?: unknown, headers: Record<string,string> = {}): Promise<any> {
  const epoch = identityEpoch;
  const response = await fetch('/api' + path, {method, credentials:'same-origin', headers:{'Content-Type':'application/json', ...(csrf?{'X-CSRF-Token':csrf}:{}), ...headers}, ...(body === undefined?{}:{body:JSON.stringify(body)})});
  const data = await response.json().catch(() => ({}));
  if (epoch !== identityEpoch) throw new ApiError({code:'stale_identity',message:'已切换身份，忽略先前会话的响应'});
  if (!response.ok) { let detail=data.detail || data; if(Array.isArray(detail)) detail={code:'validation_error',message:detail.map((d:Row)=>d.msg).join('；')}; throw new ApiError(detail); }
  return data;
}
export const dateText = (value?: string) => value ? new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}) : '—';
export const labels: Record<string,string> = {no_match:'未找到适用依据',offline:'离线规则回答',offline_fallback:'离线降级',policy_conflict:'制度范围冲突',missing_context:'必要条件未齐',profile_mismatch:'员工资料待核对',approved_expected_date:'约定补交日期',approval_reason:'审批原因',onboarding_form:'入职信息表',contact_confirmation:'联系信息确认',internship_arrangement:'实习安排确认',employment_information:'任职信息确认',id:'工号',instruction:'任务说明',employee:'员工',hr:'HR 经办人',admin:'HR 管理员',it:'IT 经办人',new:'待分派',assigned:'待接单',in_progress:'处理中',waiting_employee:'待补充信息',resolved:'待员工确认',closed:'已确认完成',cancelled:'已取消',pending:'待处理',completed:'已完成',draft:'草稿',published:'已发布',withdrawn:'已撤回',MATERIAL_EXCEPTION:'材料补交申请',ACCESS_DEVICE:'账号 / 设备异常',GENERAL_ONBOARDING:'入职咨询',IT_ACCESS:'账号准备',IT_DEVICE:'设备准备',MATERIAL_FOLLOWUP:'材料补交跟进',answered:'已答复并完成处理',approved_deferred:'批准延期补交',rejected:'未批准补交',city:'工作城市',employment_type:'雇佣类型',planned_start_date:'计划入职日',description:'事项说明',missing_item:'缺失材料',expected_date:'预计补充日期',device_type:'账号或设备类型'};
export const label = (v: string) => labels[v] || v;
