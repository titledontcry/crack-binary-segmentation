const $ = selector => document.querySelector(selector);
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const state = {projects:[], projectId:null, session:null, page:1, total:0, active:null, photo:null};
const perPage = 48;
let searchTimer;

async function api(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) { const body=await response.json().catch(()=>({detail:response.statusText})); throw new Error(body.detail || response.statusText); }
  return response;
}
function message(value) { $('#message').textContent=value; $('#message').hidden=!value; }
function date(value) { return value ? new Date(value).toLocaleString('th-TH') : '—'; }
function flightLabel(flight) { return `${flight.legacy ? 'รอบเดิม' : flight.stopped_at ? 'รอบตรวจ' : '● กำลังรับภาพ'} ${flight.session}`; }
async function loadCatalog() {
  state.projects=await (await api('/api/projects')).json();
  state.active=await (await api('/api/flights/active')).json();
  $('#active-status').textContent=state.active ? `กำลังรับภาพเข้า ${state.active.session} · เริ่ม ${date(state.active.started_at)}` : 'ยังไม่มีรอบตรวจที่กำลังรับภาพ · ภาพใหม่จะถูกพักไว้ในกล่องพักภาพ';
  $('#start-flight').hidden=!!state.active; $('#stop-flight').hidden=!state.active;
  const inboxResponse=await api('/api/flights/_inbox/images?limit=1');
  $('#inbox-count').textContent=inboxResponse.headers.get('X-Total-Count')||'0';
  renderFolders();
}
function renderFolders() {
  $('#project-list').innerHTML=state.projects.map(project=>`<div><button class="folder ${state.projectId===project.id&&!state.session?'active':''}" data-project="${escapeHtml(project.id)}">▰ ${escapeHtml(project.name)} <span>${project.flights.length}</span></button>${project.flights.map(flight=>`<button class="flight ${state.session===flight.session?'active':''}" data-session="${escapeHtml(flight.session)}">${escapeHtml(flightLabel(flight))} · ${flight.image_count}</button>`).join('')}</div>`).join('');
  $('#inbox-button').classList.toggle('active',state.session==='_inbox');
  document.querySelectorAll('[data-project]').forEach(button=>button.onclick=()=>selectProject(button.dataset.project));
  document.querySelectorAll('[data-session]').forEach(button=>button.onclick=()=>selectSession(button.dataset.session));
}
function selectProject(id) {
  state.projectId=id; state.session=null; $('#image-search').hidden=true;
  const project=state.projects.find(item=>item.id===id);
  $('#selection-title').textContent=project?.name||'โครงการ'; $('#selection-subtitle').textContent=`${project?.flights.length||0} รอบตรวจ · เลือกรอบเพื่อดูภาพทั้งหมด`;
  $('#image-grid').innerHTML=''; $('#pagination').innerHTML='';
  $('#flight-list').innerHTML=project?.flights.map(flight=>`<button class="flight-card" data-open="${escapeHtml(flight.session)}"><strong>${escapeHtml(flightLabel(flight))}</strong><span>${flight.image_count} ภาพ · AI เสร็จ ${flight.done_count} · ${date(flight.started_at)}</span></button>`).join('') || '<p>ยังไม่มีรอบตรวจในโครงการนี้</p>';
  document.querySelectorAll('[data-open]').forEach(button=>button.onclick=()=>selectSession(button.dataset.open));
  renderFolders();
}
async function selectSession(session) {
  state.session=session; state.page=1; $('#image-search').value=''; $('#image-search').hidden=false;
  $('#flight-list').innerHTML='';
  const flight=state.projects.flatMap(project=>project.flights).find(item=>item.session===session);
  $('#selection-title').textContent=session==='_inbox'?'กล่องพักภาพ':flightLabel(flight||{session});
  $('#selection-subtitle').textContent=session==='_inbox'?'รับภาพไว้โดยยังไม่ส่งประมวลผล AI':'ภาพต้นฉบับและผล AI ในโฟลเดอร์นี้';
  renderFolders(); await loadImages();
}
async function loadImages() {
  if (!state.session) return;
  const selected=state.session, query=new URLSearchParams({limit:String(perPage),offset:String((state.page-1)*perPage),q:$('#image-search').value});
  const response=await api(`/api/flights/${encodeURIComponent(selected)}/images?${query}`);
  const images=await response.json();
  if (state.session!==selected) return;
  state.total=Number(response.headers.get('X-Total-Count')||0);
  $('#selection-subtitle').textContent=`${state.total} ภาพ · ${selected==='_inbox'?'พักไว้ ยังไม่ประมวลผล AI':'คลิกภาพเพื่อดูต้นฉบับและผล AI'}`;
  $('#image-grid').innerHTML=images.length ? images.map((image,index)=>`<button class="image-card" data-index="${index}" type="button"><img src="${escapeHtml(image.overlay_url||image.original_url)}" alt="${escapeHtml(image.filename)}" loading="lazy"><div><strong title="${escapeHtml(image.filename)}">${escapeHtml(image.filename)}</strong><small>${escapeHtml(image.status)} · ${date(image.captured_at||image.received_at)}</small></div></button>`).join('') : '<p>ไม่มีภาพในรอบนี้</p>';
  document.querySelectorAll('.image-card').forEach(button=>button.onclick=()=>openPhoto(images[Number(button.dataset.index)]));
  const pages=Math.ceil(state.total/perPage);
  $('#pagination').innerHTML=pages>1?`<button id="previous" ${state.page===1?'disabled':''}>← ก่อนหน้า</button><span>หน้า ${state.page} / ${pages}</span><button id="next" ${state.page===pages?'disabled':''}>ถัดไป →</button>`:'';
  if(pages>1){$('#previous').onclick=()=>{state.page--;loadImages()};$('#next').onclick=()=>{state.page++;loadImages()};}
}
function openPhoto(image) {
  state.photo=image;
  $('#photo-large').src=image.overlay_url||image.original_url;
  $('#photo-name').textContent=image.filename;
  $('#photo-meta').textContent=`${date(image.captured_at||image.received_at)} · ${image.status}`;
  $('#photo-original').href=image.original_url;
  $('#photo-mask').hidden=!image.mask_url;
  if(image.mask_url) $('#photo-mask').href=image.mask_url;
  $('#photo-review').hidden=image.status!=='done';
  $('#review-current').textContent=`คนตรวจ: ${image.review_status==='pending'?'ยังไม่ตรวจ':image.review_status+(image.reviewer_name?' · '+image.reviewer_name:'')}`;
  $('#photo-dialog').showModal();
}
document.querySelectorAll('[data-decision]').forEach(button=>button.onclick=async()=>{
  const image=state.photo;if(!image)return;
  const reviewer=prompt('ชื่อผู้ตรวจ',localStorage.getItem('crack-reviewer')||'');
  if(!reviewer?.trim())return;
  const note=prompt('หมายเหตุ (เว้นว่างได้)','');if(note===null)return;
  try{
    const updated=await(await api(`/api/flights/${encodeURIComponent(image.session)}/image/${encodeURIComponent(image.id)}/review`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({decision:button.dataset.decision,reviewer,note})})).json();
    localStorage.setItem('crack-reviewer',reviewer);
    state.photo={...image,...updated};
    $('#review-current').textContent=`คนตรวจ: ${updated.review_status} · ${updated.reviewer_name}`;
    message('บันทึกผลคนตรวจแล้ว');
    await loadImages();
  }catch(error){message(error.message)}
});
$('#close-photo').onclick=()=>$('#photo-dialog').close();
$('#photo-dialog').onclick=event=>{if(event.target===$('#photo-dialog'))$('#photo-dialog').close()};
$('#new-project').onclick=async()=>{const name=prompt('ชื่อโครงการใหม่');if(!name?.trim())return;try{const project=await(await api('/api/projects',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})})).json();await loadCatalog();selectProject(project.id);message('สร้างโครงการแล้ว')}catch(error){message(error.message)}};
$('#start-flight').onclick=async()=>{try{let projects=state.projects.filter(project=>project.id!=='legacy');if(!projects.length){const name=prompt('ยังไม่มีโครงการ กรุณาตั้งชื่อโครงการ');if(!name?.trim())return;const created=await(await api('/api/projects',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})})).json();await loadCatalog();projects=state.projects.filter(project=>project.id===created.id)}let project=projects.find(item=>item.id===state.projectId);if(!project){const choice=prompt('เลือกโครงการที่จะเริ่มรับภาพ:\n'+projects.map((item,index)=>`${index+1}. ${item.name}`).join('\n'),'1');if(!choice)return;project=projects[Number(choice)-1];if(!project){message('หมายเลขโครงการไม่ถูกต้อง');return}}const flight=await(await api('/api/flights/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({project_id:project.id})})).json();await loadCatalog();await selectSession(flight.session);message(`เริ่มรับภาพแล้ว: ${project.name} / ${flight.session}`)}catch(error){message(error.message)}};
$('#stop-flight').onclick=async()=>{if(!state.active||!confirm(`หยุดรับภาพเข้ารอบ ${state.active.session}?\nภาพที่ agent ส่งหลังจากนี้จะเข้ากล่องพักภาพ`))return;try{await api(`/api/flights/${encodeURIComponent(state.active.id)}/stop`,{method:'POST'});await loadCatalog();message('หยุดรับภาพรอบนี้แล้ว ภาพที่ถ่ายในช่วงรอบแต่ส่งช้าจะยังถูกจัดเข้ารอบเดิม')}catch(error){message(error.message)}};
$('#inbox-button').onclick=()=>selectSession('_inbox');
$('#image-search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{state.page=1;loadImages()},250)};
loadCatalog().then(()=>{const wanted=new URLSearchParams(location.search).get('session');if(wanted)selectSession(wanted);else if(state.projects.length)selectProject(state.projects[0].id)}).catch(error=>message(error.message));
setInterval(()=>loadCatalog().then(()=>{if(state.session)loadImages()}).catch(()=>{}),15000);
