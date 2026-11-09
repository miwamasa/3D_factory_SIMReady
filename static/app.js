'use strict';
const $ = id => document.getElementById(id);
const app = {agentToken: sessionStorage.getItem('motor-agent') || '', humanToken: '', human: null, snapshot: null, proposals: [], policy: null, busy: false, tab: 'overview'};
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmtTime = sec => [Math.floor(sec / 3600), Math.floor(sec % 3600 / 60), sec % 60].map(v => String(v).padStart(2, '0')).join(':');
const pct = x => (x * 100).toFixed(1) + '%';
const statusNames = {pending:'承認待ち',ready:'自動許可',approved:'承認済み',executed:'実行済み',rejected:'却下',expired:'期限切れ',stale:'状態変更・再提案',invalidated:'リセットで無効'};
const roleNames = {supervisor:'現場責任者',planner:'生産計画担当者',agent:'エージェント',viewer:'閲覧者'};
let toastTimer;
let factoryView;
let factoryViewLoading;
let factoryTimeInProgress = false;
let snapshotEpoch = 0;
function toast(message, error = false) { $('toast').textContent = message; $('toast').className = 'visible' + (error ? ' error' : ''); clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').className = '', 4500); }
async function api(path, body, token = app.agentToken) {
  const headers = {}; if (token) headers.Authorization = 'Bearer ' + token;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const response = await fetch(path, {method: body === undefined ? 'GET' : 'POST', headers, ...(body === undefined ? {} : {body: JSON.stringify(body)})});
  const value = await response.json();
  if (!response.ok) { const e = new Error(value.message || '接続に失敗しました'); e.code = value.code; e.value = value; throw e; }
  return value;
}
const tool = (name, args = {}) => api('/api/tool', {name, arguments: args});
function output(value) { $('agent-output').textContent = JSON.stringify(value, null, 2); }
async function handle(fn) { try { await fn(); } catch(e) { toast((e.code ? e.code + ': ' : '') + e.message, true); output(e.value || {error:e.message}); } }

document.querySelectorAll('[data-tab]').forEach(btn => btn.addEventListener('click', () => {
  app.tab = btn.dataset.tab;
  document.querySelectorAll('[data-tab]').forEach(b => b.classList.toggle('active', b === btn));
  document.querySelectorAll('.page').forEach(p => p.classList.toggle('hidden', p.id !== 'page-' + app.tab));
  $('page-name').textContent = btn.textContent.replace(/\d+/g, '').replace(/[▦⌘✓⇄]/g, '').trim();
  if (app.tab === 'factory3d') {
    $('page-name').textContent = '工場 3D';
    openFactoryView();
  }
  refresh();
}));

function syncFactoryView() {
  factoryView?.update(app.snapshot, {
    supervisor: app.human?.role === 'supervisor',
    online: document.body.dataset.telemetry === 'live',
  });
}

async function changeFactoryTime(request) {
  factoryTimeInProgress = true;
  snapshotEpoch += 1;
  try {
    const snapshot = await request();
    renderSnapshot(snapshot);
    syncFactoryView();
    return snapshot.tick;
  } finally {
    factoryTimeInProgress = false;
  }
}

async function openFactoryView() {
  try {
    if (!factoryViewLoading) {
      factoryViewLoading = import('/factory3d/view.js').then(({ mountFactoryView }) => mountFactoryView($('page-factory3d'), {
        jump: seconds => changeFactoryTime(() => api('/api/step', { seconds }, app.humanToken)),
        clock: running => changeFactoryTime(() => control('clock', running)),
      }));
    }
    factoryView = await factoryViewLoading;
    syncFactoryView();
  } catch (error) {
    $('factory3d-loading').textContent = '3D画面の読込みに失敗しました。サーバー起動後に再読み込みしてください。';
    factoryViewLoading = null;
  }
}

function motorIcon(index) {
  const machines = [
    '<path class="machine-body" d="M19 25h34v57H19zM56 43h43v39H56z"/><path d="M25 34h22M25 41h22M25 48h22M25 55h22M25 62h22"/><path class="machine-dark" d="M61 49h31v25H61z"/><path d="M67 62h18m-9-8v16M35 82v9m46-9v9"/>',
    '<path class="machine-body" d="M20 24h80v60H20z"/><path class="machine-dark" d="M28 32h64v43H28z"/><circle cx="60" cy="54" r="17" class="motor-ring"/><g class="rotor"><path d="M43 54h34m-17-17v34M48 42l24 24M48 66l24-24"/></g><circle cx="60" cy="54" r="6" class="machine-dark"/><path d="M28 84v7m64-7v7"/>',
    '<path class="machine-body" d="M19 65h82v19H19zM27 40h15v25H27zM79 40h15v25H79z"/><path d="M13 52h94"/><rect class="machine-body" x="43" y="38" width="35" height="29" rx="4"/><circle class="motor-ring" cx="60" cy="53" r="9"/><path d="M48 42v20m7-20v20m11-20v20m7-20v20M25 84v7m70-7v7"/>',
    '<path class="machine-body" d="M22 27h76v57H22z"/><path class="machine-dark" d="M31 38h58v34H31z"/><path d="M35 29v-9h50v9M60 21v22m-8-2h16v8H52z"/><path class="machine-body" d="M43 55h34v16H43z"/><path d="M27 84v7m66-7v7M37 53h-7m61 0h-8"/>',
    '<path class="machine-body" d="M20 19h13v65H20zM87 19h13v65H87zM20 18h80v14H20z"/><path d="M60 32v25m-9-11h18v10H51zM60 56v11"/><path class="machine-body" d="M36 69h48v15H36z"/><path d="M45 68h30M26 84v7m68-7v7"/><path class="signal" d="M57 59h6v6h-6z"/>',
    '<path class="machine-body" d="M20 29h80v55H20z"/><path class="machine-dark" d="M30 37h60v28H30z"/><path d="M34 53h9l5-9 10 16 7-13 6 6h14M30 72h28M30 77h19M27 84v7m66-7v7"/><circle class="signal" cx="86" cy="74" r="3"/>'
  ];
  return `<svg class="station-svg" viewBox="0 0 120 110" aria-hidden="true"><g fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="square" stroke-linejoin="round"><path class="conveyor" d="M2 91h116v9H2z"/><path d="M9 95h102" stroke-dasharray="5 5"/><path d="M19 100v7m82-7v7"/>${machines[index]}<path d="M106 24v52" stroke="#526c80"/><rect class="signal" x="102" y="24" width="8" height="15" rx="1"/></g></svg>`;
}
function stationMode(s, st, i) {
  const unit=st.queue[0];
  if(st.fault)return 'fault';
  if(i===4 && s.scenario==='torque_drift')return 'warning';
  if(!s.running)return 'stopped';
  if(unit && unit.elapsed>=st.cycle && i<5 && s.stations[i+1].queue.length>=3)return 'blocked';
  return unit?'processing':'idle';
}
const modeLabels={fault:'異常停止',warning:'工具ドリフト',stopped:'ライン停止',blocked:'搬送待ち',processing:'加工中',idle:'待機中'};
function renderLine(s) {
  const focusedStation=document.activeElement?.dataset.station;
  $('line-flow').innerHTML=s.stations.map((st,i)=>{
    const unit=st.queue[0], mode=stationMode(s,st,i), progress=unit?Math.round(unit.elapsed/st.cycle*100):0;
    const dot=mode==='fault'?'red':['blocked','warning'].includes(mode)?'amber':mode==='processing'?'green':'gray';
    return `<button type="button" class="station ${mode}" data-station="${st.id}" aria-label="${esc(st.id+' '+st.name+' '+modeLabels[mode]+' の設備詳細')}"><div class="station-top"><span>${st.id} / M-${101+i}</span><i class="dot ${dot}"></i></div>${motorIcon(i)}<h3>${esc(st.name)}</h3><div class="station-status"><span>${modeLabels[mode]}</span><span>${progress}%</span></div><div class="progress"><i data-progress="${progress}"></i></div><div class="station-stats"><span>TI-${101+i}</span><span>${st.temperature.toFixed(1)} °C</span></div><div class="station-stats"><span>CYCLE</span><span>${st.cycle} s</span></div><div class="station-stats"><span>SERIAL</span><span>${unit?'M'+String(unit.serial).padStart(5,'0'):'—'}</span></div><div class="station-queue">${[0,1,2].map(n=>`<i class="${n<st.queue.length?'filled':''}"></i>`).join('')}<span>WIP ${st.queue.length}/3</span></div></button>`;
  }).join('');
  $('line-flow').querySelectorAll('[data-progress]').forEach(el=>el.style.width=el.dataset.progress+'%');
  if(focusedStation)$('line-flow').querySelector(`[data-station="${focusedStation}"]`)?.focus({preventScroll:true});
  const interlock=Boolean(s.stations[2].fault);
  $('interlock-dot').className='dot '+(interlock?'red':'green');
  $('interlock-status').textContent=interlock?'ACTIVE / 起動禁止':'CLEAR / 起動許可';
  renderFaceplate();
}
function renderFaceplate() {
  if(!$('equipment-dialog').open || !app.selectedStation || !app.snapshot)return;
  const s=app.snapshot, i=s.stations.findIndex(st=>st.id===app.selectedStation),st=s.stations[i];
  if(!st)return;
  const mode=stationMode(s,st,i),unit=st.queue[0];
  $('equipment-title').textContent=st.id+' / '+st.name;
  const values=[['STATE',modeLabels[mode]],['TEMPERATURE',st.temperature.toFixed(1)+' °C'],['CYCLE TIME',st.cycle+' s'],['WIP',st.queue.length+' / 3'],['COMPLETED',st.completed+' units'],['FAULT',st.fault||'NONE']];
  $('equipment-content').innerHTML=`<div class="faceplate-grid">${values.map(([label,value])=>`<div class="faceplate-value"><span>${label}</span><strong class="${label==='STATE'&&mode==='fault'?'fault-value':''}">${esc(value)}</strong></div>`).join('')}</div><div class="faceplate-queue">SIM TIME ${fmtTime(s.tick)}<br>工程内シリアル: ${st.queue.length?st.queue.map(u=>'M'+String(u.serial).padStart(5,'0')).join(' / '):'なし'}<br>加工進捗: ${unit?unit.elapsed:0} / ${st.cycle} s</div>`;
}
$('line-flow').addEventListener('click',event=>{const button=event.target.closest('[data-station]');if(!button)return;app.selectedStation=button.dataset.station;$('equipment-dialog').showModal();renderFaceplate();});
$('close-equipment').addEventListener('click',()=>$('equipment-dialog').close());
function renderChart(s) {
  const history = [{tick:0,good:0,reject:0},...s.history];
  if (history[history.length-1].tick !== s.tick) history.push({tick:s.tick,good:s.good,reject:s.reject});
  const w=530,h=128,left=26,bottom=107,top=8,right=520;
  const max = Math.max(5,...history.map(p => p.good)), xMax = Math.max(60,s.tick);
  const path = key => history.map((p,i) => `${i?'L':'M'}${left + p.tick / xMax*(right-left)},${bottom-p[key]/max*(bottom-top)}`).join(' ');
  const grid = [0,.5,1].map(v => `<line x1="${left}" x2="${right}" y1="${bottom-v*(bottom-top)}" y2="${bottom-v*(bottom-top)}" stroke="#304959" stroke-dasharray="3 4"/><text x="2" y="${bottom-v*(bottom-top)+3}">${Math.round(v*max)}</text>`).join('');
  $('production-chart').innerHTML = `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="良品と不良の累積生産数">${grid}<path d="${path('good')} L${left+history[history.length-1].tick/xMax*(right-left)},${bottom} L${left},${bottom}Z" fill="#203b3e"/><path d="${path('good')}" fill="none" stroke="#6bd3a0" stroke-width="2"/><path d="${path('reject')}" fill="none" stroke="#f08f84" stroke-width="1.5"/><text x="${left}" y="126">0</text><text x="${right-40}" y="126">${(xMax/60).toFixed(1)} min</text></svg>`;
}
function renderSnapshot(s) {
  app.snapshot=s;
  $('sim-time').textContent=fmtTime(s.tick); $('seed-label').textContent='SEED '+s.seed;
  $('good-kpi').textContent=s.good; $('reject-kpi').textContent=s.reject;
  $('throughput').textContent=s.kpi.throughput_per_hour+' / h';
  $('quality-kpi').textContent=s.good+s.reject?pct(s.kpi.quality):'—'; $('wip-kpi').textContent=s.kpi.wip;
  $('oee-kpi').textContent=s.tick?pct(s.kpi.oee_estimate):'—';
  $('oee-detail').textContent=`A ${pct(s.kpi.availability)} · P ${pct(s.kpi.performance)}`;
  $('quality-track').style.width=(s.kpi.quality*100)+'%'; $('wip-track').style.width=(s.kpi.wip/18*100)+'%'; $('oee-track').style.width=(s.kpi.oee_estimate*100)+'%';
  $('line-status').textContent=s.running?'稼働中':'一時停止'; $('line-dot').className='dot '+(s.running?'green':'gray');
  $('clock-button').textContent=s.clock_running?'時計を停止':'時計を再開';
  if (document.activeElement !== $('speed-select')) $('speed-select').value=s.speed;
  $('energy-label').textContent=s.energy_kwh.toFixed(2)+' kWh';
  $('inventory').innerHTML=`${s.inventory} <small>セット${s.deliveries.length?' · 入荷待ち '+s.deliveries.reduce((n,d)=>n+d.quantity,0):''}</small>`;
  $('torque-label').innerHTML=`${s.torque_nm.toFixed(1)} <small>N·m</small>`;
  const alarms=s.alarms.filter(a=>a.active);
  $('alarm-badge').textContent=alarms.length?alarms.length+' ACTIVE':'NORMAL';
  const critical=alarms.some(a=>a.code==='BEARING_HEAT');
  $('annunciator').className='annunciator'+(alarms.length?(critical?' critical':' warning'):'');
  $('annunciator-code').textContent=alarms.length?(critical?'INTERLOCK':'WARNING'):'NORMAL';
  $('annunciator-message').textContent=alarms.length?alarms[alarms.length-1].message:'アクティブなアラームはありません';
  $('annunciator-count').textContent=alarms.length+' ACTIVE';
  $('alarm-badge').className='tag'+(alarms.length?' alarm-active':'');
  $('alarms').innerHTML=alarms.length?alarms.map(a=>`<div class="alarm ${a.code==='BEARING_HEAT'?'critical-alarm':''}"><time>t=${a.tick}s</time><strong>${esc(a.code)} · ${a.station}</strong><p>${esc(a.message)}</p></div>`).join(''):'<div class="normal-state"><div class="normal-icon">✓</div><div><strong>ラインは安定しています</strong><p>現在、アクティブなアラームはありません</p></div></div>';
  $('orders').innerHTML=Object.values(s.orders).map(o=>`<tr><td>${o.id}</td><td>${o.model}</td><td>P${o.priority}</td><td>${o.quantity} / ${o.started}</td><td>${o.good} / ${o.reject}</td><td><div class="order-progress"><div class="progress"><i data-progress="${(o.good+o.reject)/o.quantity*100}"></i></div><span>${Math.round((o.good+o.reject)/o.quantity*100)}%</span></div></td><td><span class="order-status ${o.status}">${({planned:'計画中',released:'リリース済',completed:'完了'})[o.status]}</span></td></tr>`).join('');
  $('orders').querySelectorAll('[data-progress]').forEach(el=>el.style.width=el.dataset.progress+'%');
  renderLine(s); renderChart(s);
  $('last-updated').textContent='SYNC '+new Date().toLocaleTimeString('ja-JP');
  const supervisor=app.human?.role==='supervisor';
  ['clock-button','speed-select','step-button','reset-button','scenario-select','scenario-button'].forEach(id=>$(id).disabled=!supervisor);
}
function proposalCard(p, human) {
  const label=app.policy?.actions[p.action]?.label || p.action;
  const canApprove=human && p.status==='pending' && app.human?.role===p.required_role;
  const canExecute=!human && ['ready','approved'].includes(p.status);
  const before=p.impact.before === undefined?'':`変更: ${p.impact.before} → ${p.impact.after} ${p.impact.unit||''} · `;
  return `<article class="proposal-card"><div class="proposal-title"><strong>${esc(label)}</strong><span class="subtle">${esc(p.target)}</span><span class="tag status-${p.status}">${statusNames[p.status]||esc(p.status)}</span></div><div class="proposal-detail"><span>${esc(p.principal)}</span><span>承認: ${roleNames[p.required_role]||'不要'}</span><span>期限 ${new Date(p.expires_at*1000).toLocaleTimeString('ja-JP')}</span><span>ID ${p.id.slice(0,8)}</span></div><code>${esc(p.action)} ${esc(JSON.stringify(p.arguments))}</code><p>${esc(before+(p.impact.note|| (p.impact.cost_jpy?'手配額 '+p.impact.cost_jpy.toLocaleString()+'円 / 入荷まで60秒（仮想）':'')))}</p><p class="form-hint">承認対象のダイジェスト: ${p.digest.slice(0,20)}…</p>${p.decision?`<p>判断: ${esc(p.decision.approver)} / ${esc(p.decision.reason)}</p>`:''}${canApprove?`<textarea rows="2" data-reason="${p.id}" placeholder="確認内容・判断理由を3文字以上で入力" aria-label="承認理由"></textarea><div class="form-buttons"><button class="button primary" data-approve="${p.id}">内容を確認して承認</button><button class="button" data-reject="${p.id}">却下</button></div>`:''}${canExecute?`<div class="form-buttons"><button class="button primary" data-execute="${p.id}">${p.status==='approved'?'承認済みの操作を実行':'自動許可の操作を実行'}</button></div>`:''}${human && p.status==='pending'&&!canApprove?`<p class="form-hint">${roleNames[p.required_role]}のキーで接続すると判断できます。</p>`:''}</article>`;
}
function renderProposals(proposals) {
  app.proposals=proposals;
  $('approval-count').textContent=proposals.filter(p=>p.status==='pending').length;
  const signature=JSON.stringify([proposals,app.human?.id]);
  if(app.proposalSignature===signature)return;
  app.proposalSignature=signature;
  // Preserve a human's in-progress reason while polling.
  const drafts=new Map([...document.querySelectorAll('[data-reason]')].map(el=>[el.dataset.reason,el.value]));
  const focused=document.activeElement?.dataset.reason, caret=document.activeElement?.selectionStart;
  $('agent-proposals').innerHTML=proposals.length?proposals.map(p=>proposalCard(p,false)).join(''):'<div class="empty">まだ操作案はありません。操作を選んでpreflightしてください。</div>';
  const pending=proposals.filter(p=>p.required_role);
  $('human-proposals').innerHTML=pending.length?pending.map(p=>proposalCard(p,true)).join(''):'<div class="empty">承認を必要とする操作案はありません。</div>';
  document.querySelectorAll('[data-reason]').forEach(el=>{el.value=drafts.get(el.dataset.reason)||'';if(el.dataset.reason===focused){el.focus();el.setSelectionRange(caret,caret);}});
}
function renderAudit(events) {
  $('audit-log').innerHTML=events.slice(0,25).map(e=>`<div class="audit-row ${e.event==='denied'?'denied':''}"><span>t=${e.tick}s</span><strong>${esc(e.event)}</strong><span>${esc(e.actor)}</span><code>${esc(e.data.code||e.data.action||e.data.proposal_id?.slice(0,12)||e.data.value||'')}</code></div>`).join('')||'<div class="empty">操作・承認・拒否がここに記録されます。</div>';
}
async function refresh() {
  if(app.busy||!app.agentToken||factoryTimeInProgress) return; app.busy=true;
  const requestedEpoch = snapshotEpoch;
  try {
    const results=await Promise.all([api('/api/snapshot'),api('/api/proposals'),api('/api/audit')]);
    if (requestedEpoch !== snapshotEpoch) return;
    renderSnapshot(results[0]);renderProposals(results[1].proposals);renderAudit(results[2].events);
    $('connection').textContent='COMMS OK';$('connection').style.color='';document.body.dataset.telemetry='live';
  } catch(e) {
    if (requestedEpoch !== snapshotEpoch) return;
    $('connection').textContent='COMMS LOST';$('connection').style.color='var(--red)';document.body.dataset.telemetry='stale';
    $('annunciator').className='annunciator offline';$('annunciator-code').textContent='STALE DATA';
    $('annunciator-message').textContent='通信断：表示値は最終受信時の値です。再接続を待っています';
    $('annunciator-count').textContent='OFFLINE';
  }
  finally{app.busy=false;syncFactoryView();}
}
function selectAction() {
  const action=$('action-select').value, spec=app.policy.actions[action];
  $('target-input').value=spec.target==='order'?'WO-2602':spec.target;
  const defaults={'mes.set_torque':{torque_nm:4.2},'mes.maintenance':{station_id:'S05'},'erp.set_priority':{priority:5},'erp.replenish':{quantity:80}};
  $('args-input').value=JSON.stringify(defaults[action]||{},null,2);
}
async function preflight(action,target,args) { const result=await tool('factory_preflight',{action,target,arguments:args}); output(result); await refresh(); toast(result.status==='ready'?'自動許可の操作案を作成しました':'操作案を作成しました。人の承認を待っています'); return result.proposal; }
async function execute(id) { const key='ui-'+id; const result=await tool('factory_execute',{proposal_id:id,idempotency_key:key});output(result);toast('操作を実行しました');await refresh();return result; }
$('action-select').addEventListener('change',selectAction);
$('preflight-button').addEventListener('click',()=>handle(async()=>{let args;try{args=JSON.parse($('args-input').value);}catch(e){throw new Error('引数のJSONを確認してください');}await preflight($('action-select').value,$('target-input').value,args);}));
$('suggest-button').addEventListener('click',()=>handle(async()=>{
  const proposals=(await tool('factory_list_proposals')).proposals;
  const executable=proposals.find(p=>['ready','approved'].includes(p.status));
  if(executable) return execute(executable.id);
  if(proposals.some(p=>p.status==='pending')) {output({status:'waiting_for_human',message:'人の承認を待っています。人の承認タブで確認してください。'});return toast('人の承認を待っています');}
  const s=await tool('mes_get_snapshot');
  if(['torque_drift','bearing_heat'].includes(s.scenario)) {
    if(s.running) {const p=await preflight('mes.pause_line','LINE-01',{});await execute(p.id);}
    return preflight('mes.maintenance','LINE-01',{station_id:s.scenario==='bearing_heat'?'S03':'S05'});
  }
  if(!s.running) return preflight('mes.resume_line','LINE-01',{});
  if(s.inventory<10 && !s.deliveries.length) return preflight('erp.replenish','MOTOR-KIT',{quantity:80});
  if(s.orders['WO-2602'].status==='planned') return preflight('erp.release_order','WO-2602',{});
  output({status:'monitoring',message:'異常はありません。mes_get_snapshotで監視を継続します。'});toast('状態を観測しました。追加操作は不要です');
}));
$('deny-test-button').addEventListener('click',()=>handle(async()=>{
  try{await api('/api/approval',{proposal_id:app.proposals[0]?.id||'no-proposal',decision:'approve',reason:'agentによる自己承認テスト'});toast('想定外: 承認が受理されました',true);}
  catch(e){output({test:'agent_self_approval',expected:'FORBIDDEN',actual:e.code,passed:e.code==='FORBIDDEN',response:e.value});toast('自己承認はサーバーで拒否されました');await refresh();}
}));
document.addEventListener('click',event=>{
  const button=event.target.closest('button');if(!button)return;
  if(button.dataset.execute)handle(()=>execute(button.dataset.execute));
  const id=button.dataset.approve||button.dataset.reject;
  if(id)handle(async()=>{
    const reason=document.querySelector(`[data-reason="${id}"]`).value;
    await api('/api/approval',{proposal_id:id,decision:button.dataset.approve?'approve':'reject',reason},app.humanToken);
    toast(button.dataset.approve?'承認しました。エージェント側から実行できます':'操作案を却下しました');await refresh();
  });
});
function showIdentity() {$('identity-dialog').showModal();$('human-key').focus();}
$('identity-button').addEventListener('click',showIdentity);document.querySelectorAll('.connect-human').forEach(el=>el.addEventListener('click',showIdentity));
$('close-dialog').addEventListener('click',()=>$('identity-dialog').close());
function renderIdentity(){const text=app.human?roleNames[app.human.role]+' · '+app.human.id:'人のキーで接続';$('identity-button').textContent=text;$('human-status').textContent=app.human?'接続中: '+text:'承認者は未接続です';}
$('identity-form').addEventListener('submit',e=>{e.preventDefault();handle(async()=>{
  try{const token=$('human-key').value.trim(),human=await api('/api/whoami',undefined,token);
    if(!['supervisor','planner'].includes(human.role))throw new Error('現場責任者または生産計画担当者のキーが必要です');
    app.humanToken=token;app.human=human;$('human-key').value='';$('identity-error').textContent='';$('identity-dialog').close();renderIdentity();await refresh();toast('別の主体として認証しました');
  }catch(err){$('identity-error').textContent=err.message;}
});});
$('disconnect-button').addEventListener('click',()=>{app.humanToken='';app.human=null;renderIdentity();$('identity-dialog').close();refresh();});
const control=(operation,value)=>api('/api/control',{operation,value},app.humanToken);
$('clock-button').addEventListener('click',()=>handle(async()=>{await control('clock',!app.snapshot.clock_running);await refresh();}));
$('speed-select').addEventListener('change',()=>handle(async()=>{await control('speed',Number($('speed-select').value));await refresh();}));
$('step-button').addEventListener('click',()=>handle(async()=>{await api('/api/step',{seconds:60},app.humanToken);await refresh();}));
$('reset-button').addEventListener('click',()=>handle(async()=>{if(confirm('工場状態を同じseedで初期化します。未実行の操作案は無効になります。監査ログは保持します。')){await control('reset',app.snapshot.seed);await refresh();toast('工場状態を初期化しました');}}));
$('scenario-button').addEventListener('click',()=>handle(async()=>{await control('scenario',$('scenario-select').value);await refresh();toast('実験シナリオを適用しました');}));
async function start(){
  $('mcp-url').textContent=location.origin+'/mcp';
  const config=await api('/api/config',undefined,'');
  if(!app.agentToken&&config.demo){app.agentToken=(await api('/api/demo-agent',{},'')).token;sessionStorage.setItem('motor-agent',app.agentToken);}
  if(!app.agentToken){const token=prompt('エージェントのBearerキーを入力してください（python3 credentials.py agent）');if(token){app.agentToken=token.trim();sessionStorage.setItem('motor-agent',app.agentToken);}else throw new Error('エージェントのキーが必要です');}
  try { app.policy=await tool('factory_get_policy'); } catch(e){sessionStorage.removeItem('motor-agent');throw e;}
  $('action-select').innerHTML=Object.entries(app.policy.actions).map(([key,spec])=>`<option value="${key}">L${spec.level} · ${spec.label}</option>`).join('');selectAction();
  const catalog=[['mes_get_snapshot','L3の設備・工程・品質を観測'],['erp_get_orders','L4の製造指図・実績を参照'],['erp_get_inventory','L4の在庫・入荷予定を参照'],['factory_get_policy','公開操作と許可範囲を参照'],['factory_preflight','変更内容を固定した操作案を作成'],['factory_list_proposals','操作案・承認状態を参照'],['factory_execute','承認と状態を再検証し実行'],['factory_get_audit','操作・承認・拒否の履歴を参照']];
  $('tool-catalog').innerHTML=catalog.map(([name,desc])=>`<div class="tool"><code>${name}</code><p>${desc}</p></div>`).join('');
  await refresh();setInterval(refresh,1500);
}
handle(start);
if (location.hash === '#factory3d') document.querySelector('[data-tab="factory3d"]').click();
