const $ = s => document.querySelector(s);
const isReviewPage = window.location.pathname === '/review';
const initialReviewFilter = new URLSearchParams(window.location.search).get('review');
const failedReviewIds = new Map();
let resultsRequest = 0, defaultFilterPending = !initialReviewFilter;
let rows = [], startedAt = Date.now(), activeFilter = 'all', activeReview = 'all', dashboardReviewFilter = ['all','unsubmitted','pending','reviewed'].includes(initialReviewFilter) ? initialReviewFilter : 'unsubmitted', page = 1, pageSize = 25, reviewPage = 1, reviewPageSize = 25;
const selectedForReview = new Set();
let lastSelectedId = null, previewClickTimer = null;
let detailRows = [];
let dashboardTotal = 0, dashboardSummary = {}, selectionConfirming = false, selectionSending = false, toastTimer = null, undoIds = [];
let reviewFocusId = new URLSearchParams(window.location.search).get('image'), previewState = {scale:1,x:0,y:0,drag:null,overlay:true,hasOverlay:false};
let featuredRow = null, overviewMode = 'ai';
let reviewState = { row: null, drawing: false, tool: 'pen', history: [], last: null, paths: [[]], activePath: 0, selected: null, penDrag: null };
// Filled from config.yaml via /api/settings. Null means severity is not configured.
let CARD_SEVERITY = { warningWidthPx: null, criticalWidthPx: null };
const displayTime = v => v ? new Date(v).toLocaleString('th-TH') : '—';
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const reviewLabel = value => ({pending:'รอคนตรวจ',confirmed:'ยืนยัน',corrected:'แก้ไขแล้ว',rejected:'ไม่ใช่รอยร้าว',uncertain:'ไม่แน่ใจ'})[value] || 'รอคนตรวจ';

function measure(m) {
  if (!m) return 'กำลังรอผล…'; if (!m.crack_pixels) return 'ไม่พบรอยร้าว';
  const length = m.length_mm != null ? `${(m.length_mm / 1000).toFixed(2)} m` : `${m.length_px.toFixed(0)} px`;
  const width = m.width_mm_max != null ? `${m.width_mm_max.toFixed(2)} mm` : `${m.width_px_max.toFixed(1)} px`;
  return `L ${length} · max ${width}`;
}
function v(value, suffix='—') { return value == null ? suffix : value; }
function telemetry(m = {}) {
  $('#speed-value').textContent=v(m.speed_mps != null ? `${(m.speed_mps*3.6).toFixed(1)} km/h` : null);
  $('#height-value').textContent=v(m.height_m != null ? `${m.height_m.toFixed(1)} m` : null);
  $('#distance-value').textContent=v(m.distance_to_surface_m != null ? `${m.distance_to_surface_m.toFixed(1)} m` : null);
  $('#battery-value').textContent=m.battery_pct != null ? `${m.battery_pct}%` : '—';
  $('#battery-detail').textContent=m.battery_voltage_v != null ? `${m.battery_voltage_v.toFixed(1)} V` : m.battery_pct != null ? 'จาก metadata ของภาพ' : 'ยังไม่มี telemetry';
  $('#battery-bar').style.width=m.battery_pct != null ? `${Math.max(0,Math.min(100,m.battery_pct))}%` : '0%';
}
function renderOverview(row, summary, session) {
  featuredRow = row || null;
  $('#overview-total').textContent=summary.all ?? 0;
  $('#overview-done').textContent=summary.done ?? 0;
  $('#overview-pending').textContent=summary.review?.pending ?? 0;
  $('#overview-session').textContent=session ? `รอบตรวจ ${session}` : 'รอข้อมูลรอบบิน';
  $('#sidebar-session-id').textContent=session || 'ยังไม่มีรอบตรวจ';
  $('#featured-filename').textContent=row?.filename || 'ยังไม่มีภาพตรวจ';
  $('#featured-time').textContent=row ? displayTime(row.captured_at || row.received_at) : '—';
  $('#featured-open').disabled=!row;
  $('#featured-open').textContent=row?.historical ? 'เปิดภาพต้นฉบับ ↗' : 'เปิดรายละเอียด ↗';
  $('#featured-origin').hidden=!row?.historical;
  $('#featured-origin').textContent=row?.historical ? `รอบก่อน ${row.session}` : '';
  const image=$('#featured-image');
  if(row?.overlay_url && image.dataset.imageId !== row.id){image.src=row.overlay_url;image.dataset.imageId=row.id;}
  image.hidden=!row || overviewMode!=='ai';
  $('#featured-empty').hidden=!!row || overviewMode!=='ai';
  telemetry(row?.metadata || {});
  const m=row?.measurement || {};
  $('#overview-cracks').textContent=Number.isFinite(m.component_count) ? m.component_count.toLocaleString('th-TH') : '—';
  $('#overview-length').textContent=Number.isFinite(m.length_mm) ? `${(m.length_mm/1000).toFixed(2)} m` : Number.isFinite(m.length_px) ? `${m.length_px.toFixed(0)} px` : '—';
  $('#overview-width').textContent=Number.isFinite(m.width_mm_max) ? `${m.width_mm_max.toFixed(2)} mm` : Number.isFinite(m.width_px_max) ? `${m.width_px_max.toFixed(1)} px` : '—';
  $('#overview-measure-note').textContent=Number.isFinite(m.gsd_mm_per_px) ? `GSD ${m.gsd_mm_per_px.toFixed(3)} mm/px · ค่าประมาณจากภาพ` : 'หน่วยจริงจะแสดงเมื่อมี GSD จากกล้องและระยะถ่าย';
  const lat=Number(row?.metadata?.latitude), lon=Number(row?.metadata?.longitude), hasGps=row?.metadata?.latitude!=null && row?.metadata?.longitude!=null && Number.isFinite(lat) && Number.isFinite(lon) && Math.abs(lat)<=90 && Math.abs(lon)<=180;
  $('#overview-coordinates').textContent=hasGps ? `${lat.toFixed(6)}, ${lon.toFixed(6)}` : 'พิกัด: —';
  $('#overview-map').classList.toggle('has-gps',!!hasGps);
  $('#overview-map').innerHTML=hasGps ? `<span>⌖</span><p>${lat.toFixed(6)}, ${lon.toFixed(6)}</p>` : '<span>⌖</span><p>ยังไม่มีพิกัด GPS</p>';
  const link=$('#overview-map-link');link.hidden=!hasGps;if(hasGps)link.href=`https://www.openstreetmap.org/?mlat=${encodeURIComponent(lat)}&mlon=${encodeURIComponent(lon)}#map=17/${encodeURIComponent(lat)}/${encodeURIComponent(lon)}`;
}
function setOverviewMode(mode) {
  overviewMode=mode;
  $('#show-ai').classList.toggle('active',mode==='ai');$('#show-live').classList.toggle('active',mode==='live');
  $('#featured-image').hidden=mode!=='ai'||!featuredRow;
  $('#featured-empty').hidden=mode!=='ai'||!!featuredRow;
  $('#live-image').hidden=mode!=='live'||$('#live-image').dataset.available!=='true';
  $('#live-empty').hidden=mode!=='live'||$('#live-image').dataset.available==='true';
}
function statusLabel(status) { return ({queued:'รอคิว',processing:'กำลังประมวลผล',done:'เสร็จแล้ว',failed:'ผิดพลาด'})[status] || status; }
function cardTime(value) { const date=value&&new Date(value); return date&&!Number.isNaN(date.valueOf()) ? date.toLocaleTimeString('th-TH',{hour:'2-digit',minute:'2-digit',second:'2-digit'}) : '—'; }
function pxMeasurement(measurement) {
  if (!measurement) return {text:'กำลังประมวลผล…', width:null};
  if (!measurement.crack_pixels) return {text:'ไม่พบรอยร้าว', width:0};
  const length=Number.isFinite(measurement.length_px) ? `L ${measurement.length_px.toFixed(0)} px` : 'L —';
  const width=Number.isFinite(measurement.width_px_max) ? measurement.width_px_max : null;
  return {text:length, width};
}
function severityFor(measurement) {
  const width=measurement?.width_px_max;
  if (!Number.isFinite(width)) return {level:'unknown', label:'ยังไม่มีค่าความกว้าง'};
  if (!Number.isFinite(CARD_SEVERITY.warningWidthPx) || !Number.isFinite(CARD_SEVERITY.criticalWidthPx)) return {level:'unknown', label:`max ${width.toFixed(1)} px (ยังไม่ได้ตั้งเกณฑ์)`};
  if (width >= CARD_SEVERITY.criticalWidthPx) return {level:'critical', label:`สำคัญ: max ${width.toFixed(1)} px`};
  if (width >= CARD_SEVERITY.warningWidthPx) return {level:'warning', label:`เฝ้าระวัง: max ${width.toFixed(1)} px`};
  return {level:'normal', label:`ปกติ: max ${width.toFixed(1)} px`};
}
function configureSeverity(settings) {
  const warning=Number(settings?.warning_width_px), critical=Number(settings?.critical_width_px);
  CARD_SEVERITY = warning > 0 && critical >= warning ? {warningWidthPx:warning, criticalWidthPx:critical} : {warningWidthPx:null, criticalWidthPx:null};
}
function cardStatus(row) {
  if (row.status !== 'done') return {kind:row.status, text:statusLabel(row.status)};
  if (row.review_requested || (row.review_status && row.review_status!=='pending')) return {kind:row.review_status || 'pending', text:reviewLabel(row.review_status)};
  return null;
}

function render(data, total, summary) {
  rows = data; dashboardTotal=total; dashboardSummary=summary; const review = summary.review || {};
  $('#all-count').textContent=summary.all ?? total; $('#done-count').textContent=summary.done ?? 0; $('#processing-count').textContent=summary.processing ?? 0; $('#queued-count').textContent=summary.queued ?? 0; $('#failed-count').textContent=summary.failed ?? 0;
  ['pending','confirmed','corrected','rejected','uncertain'].forEach(key => { const el=$(`#review-${key}-count`); if (el) el.textContent=review[key] ?? 0; });
  $('#review-unsubmitted-count').textContent=review.unsubmitted ?? 0;
  $('#review-pending-dashboard-count').textContent=review.pending ?? 0;
  $('#reviewed-count').textContent=review.reviewed ?? 0;
  $('#review-all-dashboard-count').textContent=summary.all;
  document.querySelectorAll('.review-filter-tab').forEach(button=>button.classList.toggle('active',button.dataset.reviewFilter===dashboardReviewFilter));
  const start=total ? (page-1)*pageSize+1 : 0, end=Math.min((page-1)*pageSize+data.length,total);
  $('#range-label').textContent=total ? `แสดง ${start}–${end} จาก ${total} ภาพ` : 'ไม่มีภาพในหมวดนี้';
  $('#rows').innerHTML=data.map((r,i)=>{
    const preview=r.status==='done' && r.overlay_url ? r.overlay_url : r.original_url;
    const status=cardStatus(r), severity=severityFor(r.measurement), timestamp=r.captured_at||r.received_at, pixels=pxMeasurement(r.measurement);
    const selectable=isSelectable(r);
    const badge=status ? `<span class="card-status ${status.kind}">${escapeHtml(status.text)}</span>` : '';
    const measureMarkup=r.status==='failed' ? `ผิดพลาด: ${escapeHtml(r.error||'ไม่ทราบสาเหตุ')}` : pixels.width == null ? escapeHtml(pixels.text) : `${escapeHtml(pixels.text)} · <span class="max-width severity-${severity.level}">max ${pixels.width.toFixed(1)} px</span>`;
    const selected=selectable && selectedForReview.has(r.id);
    return `<article class="inspection-item dashboard-card ${r.status} severity-${severity.level} ${selectable?'is-selectable':'review-locked'} ${selected?'is-selected':''}" data-id="${r.id}" tabindex="0" aria-disabled="${!selectable}" aria-pressed="${selected}"><div class="inspection-preview"><img src="${preview}" alt="ผล AI: ${escapeHtml(r.filename)}" loading="lazy" decoding="async">${badge}<button class="card-preview-button preview-icon" data-preview-id="${r.id}" type="button" aria-label="เปิดภาพใหญ่">⌕</button></div><div class="inspection-info"><div class="inspection-meta"><span class="item-number">#${start+i}</span><time>${cardTime(timestamp)}</time></div><p class="inspection-measure">${measureMarkup}</p></div></article>`;
  }).join('');
  detailRows=rows;
  if(!data.length) $('#rows').innerHTML=`<p class="results-empty">${dashboardReviewFilter==='unsubmitted'?'ไม่มีภาพที่ยังไม่ส่ง':'ไม่มีภาพในหมวดนี้'}</p>`;
  paintReviewFailures();
  document.querySelectorAll('.dashboard-card[data-id]').forEach(card=>bindDashboardCard(card, rows.find(r=>r.id===card.dataset.id)));
  document.querySelectorAll('.card-preview-button').forEach(button=>button.onclick=e=>{e.stopPropagation();detail(rows.find(r=>r.id===button.dataset.previewId));});
  updateSelectionUi();
  renderPagination(total);
}
function renderPagination(total) {
  const pages=Math.ceil(total/pageSize), holder=$('#pagination'); if(pages<=1){holder.innerHTML='';return;}
  const choices=new Set([1,pages]); for(let p=Math.max(1,page-2);p<=Math.min(pages,page+2);p++)choices.add(p);
  const sorted=[...choices].sort((a,b)=>a-b); let html=`<button data-page="${page-1}" ${page===1?'disabled':''} aria-label="หน้าก่อน">‹</button>`, previous=0;
  for(const p of sorted){if(p-previous>1)html+='<span class="page-ellipsis">…</span>';html+=`<button data-page="${p}" class="${p===page?'current':''}" ${p===page?'aria-current="page"':''}>${p}</button>`;previous=p;}
  html+=`<button data-page="${page+1}" ${page===pages?'disabled':''} aria-label="หน้าถัดไป">›</button>`; holder.innerHTML=html;
  holder.querySelectorAll('button[data-page]').forEach(b=>b.onclick=()=>{page=Number(b.dataset.page);refresh();});
}
function rowById(id) { return rows.find(row=>row.id===id); }
function isSelectable(row) { return row?.status==='done' && !row.review_requested && (!row.review_status || row.review_status==='pending'); }
function cardFor(id) { return [...document.querySelectorAll('.dashboard-card[data-id]')].find(card=>card.dataset.id===id); }
function applySelectedDom(id, selected) {
  const card=cardFor(id);
  if (!card) return;
  card.classList.toggle('is-selected',selected);
  card.setAttribute('aria-pressed',String(selected));
}
function setSelected(id, selected) {
  if(selectionSending) return;
  if (!isSelectable(rowById(id))) return;
  if (selected) selectedForReview.add(id); else selectedForReview.delete(id);
  applySelectedDom(id,selected);
}
function selectCard(id, range=false) {
  const target=rowById(id);
  if (!isSelectable(target)) return;
  const targetState=!selectedForReview.has(id);
  const first=range && lastSelectedId ? rows.findIndex(row=>row.id===lastSelectedId) : -1;
  const last=rows.findIndex(row=>row.id===id);
  if (first >= 0 && last >= 0) {
    const [from,to]=first<last?[first,last]:[last,first];
    for (let index=from;index<=to;index++) if (isSelectable(rows[index])) setSelected(rows[index].id,targetState);
  } else setSelected(id,targetState);
  lastSelectedId=id;
  updateSelectionUi();
}
function clearSelection() {
  if(selectionSending) return;
  selectedForReview.forEach(id=>applySelectedDom(id,false));
  selectedForReview.clear();
  lastSelectedId=null;
  updateSelectionUi();
}
function selectCurrentPage() {
  rows.filter(isSelectable).forEach(row=>setSelected(row.id,true));
  const last=[...rows].reverse().find(isSelectable);
  if (last) lastSelectedId=last.id;
  updateSelectionUi();
}
function updateSelectionUi() {
  const count=selectedForReview.size, clear=$('#clear-selection'), summary=$('#selection-summary');
  if (!clear || !summary) return;
  clear.disabled=!count;
  summary.textContent=count?`เลือกแล้ว ${count} ภาพ`:'ยังไม่ได้เลือก';
  const bar=$('#selection-action-bar');
  $('#selection-bar-count').textContent=`✓ เลือกแล้ว ${count} ภาพ`;
  $('#selection-confirm-text').textContent=`ส่ง ${count} ภาพ?`;
  bar.classList.toggle('is-visible',count>0);
  document.body.classList.toggle('has-selection',count>0);
  if(!count) setSelectionConfirmation(false);
}
function schedulePreviewToggle(row) {
  clearTimeout(previewClickTimer);
  previewClickTimer=setTimeout(()=>{selectCard(row.id);previewClickTimer=null;},180);
}
function focusAdjacentCard(card, key) {
  const candidates=[...document.querySelectorAll('.dashboard-card[data-id]')];
  const source=card.getBoundingClientRect(), sx=source.left+source.width/2, sy=source.top+source.height/2;
  const direction={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]}[key];
  if (!direction) return false;
  const target=candidates.map(candidate=>{const rect=candidate.getBoundingClientRect(), dx=rect.left+rect.width/2-sx, dy=rect.top+rect.height/2-sy;const forward=direction[0]?dx*direction[0]:dy*direction[1];const offset=direction[0]?Math.abs(dy):Math.abs(dx);return {candidate,score:forward>2?forward+offset*3:Infinity};}).sort((a,b)=>a.score-b.score)[0];
  if (!target || !Number.isFinite(target.score)) return false;
  target.candidate.focus(); return true;
}
function bindDashboardCard(card, row) {
  const preview=card.querySelector('.inspection-preview');
  card.onclick=event=>{
    if (event.target.closest('button')) return;
    if (event.target.closest('.inspection-preview')) {
      if (isSelectable(row)) {
        if (event.shiftKey) selectCard(row.id,true); else schedulePreviewToggle(row);
      } else detail(row);
      return;
    }
    if (isSelectable(row)) selectCard(row.id,event.shiftKey);
  };
  card.ondblclick=event=>{
    if (!event.target.closest('.inspection-preview')) return;
    clearTimeout(previewClickTimer); previewClickTimer=null; event.preventDefault(); detail(row);
  };
  card.onkeydown=event=>{
    if (event.key===' ') { event.preventDefault(); if (isSelectable(row)) selectCard(row.id,event.shiftKey); return; }
    if (event.key==='Enter') { event.preventDefault(); detail(row); return; }
    if (focusAdjacentCard(card,event.key)) event.preventDefault();
  };
  preview.onmousedown=event=>{if (event.detail>1) event.preventDefault();};
}
async function queueForReview(ids, button) {
  if (!ids.length) return;
  const oldText = button?.textContent;
  if (button) { button.disabled = true; button.textContent = 'กำลังส่ง…'; }
  try {
    return await requestReviewQueue(ids,true);
  } catch (error) {
    showReviewToast(`ส่งภาพไป Human Review ไม่สำเร็จ: ${error.message}`);
    if (button) { button.disabled = false; button.textContent = oldText; }
    return null;
  }
}
function setSelectionConfirmation(value) {
  selectionConfirming=value;
  $('#selection-action-default').hidden=value;
  $('#selection-action-confirm').hidden=!value;
}
function showReviewToast(message, ids=[], error=false) {
  const toast=$('#review-toast');
  clearTimeout(toastTimer); undoIds=ids;
  $('#review-toast-text').textContent=message;
  $('#review-toast-undo').hidden=!ids.length;
  toast.hidden=false;
  toast.classList.toggle('is-error',error);
  $('#review-toast-retry').hidden=!error;
  document.body.classList.add('has-review-toast');
  toastTimer=setTimeout(()=>{toast.hidden=true;undoIds=[];document.body.classList.remove('has-review-toast');},8000);
}
function paintReviewFailures() {
  document.querySelectorAll('.dashboard-card').forEach(card=>{
    card.querySelector('.send-warning')?.remove();
    if(failedReviewIds.has(card.dataset.id)) {
      const warning=document.createElement('span'); warning.className='send-warning'; warning.textContent='⚠ ส่งไม่สำเร็จ';
      card.querySelector('.inspection-preview').append(warning);
    }
  });
}
function matchesReviewFilter(row) {
  const stage=row.review_status && row.review_status!=='pending'?'reviewed':row.review_requested?'pending':'unsubmitted';
  return dashboardReviewFilter==='all'||dashboardReviewFilter===stage;
}
async function requestReviewQueue(ids, requested) {
  const response=await fetch('/api/images/review-queue',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({image_ids:ids,requested})});
  const body=await response.text();
  console.info('review-queue response',{url:response.url,method:'PUT',payload:{image_ids:ids,requested},status:response.status,body});
  if(!response.ok) throw new Error(response.status===422?'เลือกได้ไม่เกิน 500 ภาพต่อครั้ง':`เซิร์ฟเวอร์ส่งข้อมูลไม่สำเร็จ (HTTP ${response.status})`);
  let result; try { result=JSON.parse(body); } catch { throw new Error('เซิร์ฟเวอร์ตอบข้อมูลไม่ถูกต้อง'); }
  if(!Array.isArray(result.updated_ids)||!Array.isArray(result.skipped_ids)) throw new Error('API กับหน้าเว็บคนละรุ่น โปรด restart เซิร์ฟเวอร์แล้วตรวจสถานะภาพก่อนลองใหม่');
  return result;
}
function applyReviewQueueResult(ids, requested, requestedAt=null) {
  resultsRequest++;
  const changed=new Set(ids);
  rows.forEach(row=>{if(changed.has(row.id)) { row.review_requested=requested?1:0; if(requested) { row.review_status='pending'; row.review_requested_at=requestedAt; } else row.review_requested_at=null; }});
  const visible=rows.filter(matchesReviewFilter);
  render(visible,Math.max(0,dashboardTotal-(rows.length-visible.length)),dashboardSummary);
  void refresh();
}
async function submitSelectedForReview() {
  if(selectionSending || !selectedForReview.size) return;
  const ids=[...selectedForReview]; selectionSending=true;
  const submit=$('#selection-confirm-submit'); submit.disabled=true; submit.textContent='กำลังส่ง…';
  try {
    const result=await requestReviewQueue(ids,true), updatedIds=result.updated_ids, skippedIds=result.skipped_ids;
    updatedIds.forEach(id=>{selectedForReview.delete(id);failedReviewIds.delete(id);});
    skippedIds.forEach(id=>failedReviewIds.set(id,result.reasons?.[id]||'สถานะภาพเปลี่ยนแล้ว กรุณาตรวจสอบและลองใหม่'));
    applyReviewQueueResult(updatedIds,true,result.review_requested_at);
    lastSelectedId=null; setSelectionConfirmation(false); updateSelectionUi();
    const failed=skippedIds.length;
    const reason=[...new Set(skippedIds.map(id=>failedReviewIds.get(id)))].join(' · ');
    showReviewToast(!updatedIds.length?`ส่งไม่สำเร็จ ${failed} ภาพ: ${reason}`:failed?`ส่งแล้ว ${updatedIds.length} ภาพ · ล้มเหลว ${failed} ภาพ`:`ส่งแล้ว ${updatedIds.length} ภาพ`,updatedIds,failed>0);
  } catch(error) { ids.forEach(id=>failedReviewIds.set(id,error.message)); paintReviewFailures(); showReviewToast(`ส่งไม่สำเร็จ ${ids.length} ภาพ: ${error.message}`,[],true); }
  finally { selectionSending=false; submit.disabled=false; submit.textContent='ส่ง'; }
}
async function undoReviewQueue() {
  if(!undoIds.length) return;
  const ids=[...undoIds], button=$('#review-toast-undo'); button.disabled=true; button.textContent='กำลังย้อนกลับ…';
  try {
    const result=await requestReviewQueue(ids,false), updatedIds=result.updated_ids, skippedIds=result.skipped_ids;
    applyReviewQueueResult(updatedIds,false); undoIds=[];
    showReviewToast(skippedIds.length?`ย้อนกลับ ${updatedIds.length} ภาพ · ข้าม ${skippedIds.length} ภาพที่ตรวจแล้ว`:`ย้อนกลับ ${updatedIds.length} ภาพแล้ว`);
  } catch(error) { showReviewToast(`ย้อนกลับไม่สำเร็จ: ${error.message}`,ids); }
  finally { button.disabled=false; button.textContent='ย้อนกลับ'; }
}
function reviewCard(r,i,start){const preview=r.overlay_url||r.original_url;return `<article class="inspection-item done review-work-item" data-review-open="${r.id}" tabindex="0"><div class="inspection-preview"><img src="${preview}" alt="AI prediction: ${escapeHtml(r.filename)}"><span>AI + REVIEW</span></div><div class="inspection-info"><div class="inspection-meta"><span class="item-number">#${start+i}</span><span class="review-badge ${r.review_status||'pending'}">${reviewLabel(r.review_status)}</span><time>${displayTime(r.captured_at||r.received_at)}</time></div><h3 title="${escapeHtml(r.filename)}">${escapeHtml(r.filename)}</h3><p class="inspection-measure">${escapeHtml(measure(r.measurement))}</p></div><button class="review-now" data-review-id="${r.id}">${r.review_status==='pending'?'เริ่มตรวจ':'เปิดผลตรวจ'}</button></article>`;}
function renderReviewPagination(total){const pages=Math.ceil(total/reviewPageSize),holder=$('#review-pagination');if(pages<=1){holder.innerHTML='';return;}const choices=new Set([1,pages]);for(let p=Math.max(1,reviewPage-2);p<=Math.min(pages,reviewPage+2);p++)choices.add(p);let previous=0,html=`<button data-review-page="${reviewPage-1}" ${reviewPage===1?'disabled':''}>‹</button>`;for(const p of [...choices].sort((a,b)=>a-b)){if(p-previous>1)html+='<span class="page-ellipsis">…</span>';html+=`<button data-review-page="${p}" class="${p===reviewPage?'current':''}">${p}</button>`;previous=p;}html+=`<button data-review-page="${reviewPage+1}" ${reviewPage===pages?'disabled':''}>›</button>`;holder.innerHTML=html;holder.querySelectorAll('[data-review-page]').forEach(b=>b.onclick=()=>{reviewPage=Number(b.dataset.reviewPage);refreshReview();});}
function renderReviewPage(data,total,summary){const review=summary.review||{};$('#review-queued-count').textContent=review.queued??total;['pending','confirmed','corrected','rejected','uncertain'].forEach(key=>{$(`#review-${key}-count`).textContent=review[key]??0;});const start=total?(reviewPage-1)*reviewPageSize+1:0,end=Math.min((reviewPage-1)*reviewPageSize+data.length,total);$('#review-range-label').textContent=total?`แสดง ${start}–${end} จาก ${total} ภาพใน Human Review`:'ยังไม่มีภาพที่ส่งมาตรวจโดยคน';$('#review-rows').innerHTML=data.map((r,i)=>reviewCard(r,i,start)).join('');detailRows=data;document.querySelectorAll('[data-review-open],[data-review-id]').forEach(el=>el.onclick=()=>detail(data.find(r=>r.id===(el.dataset.reviewOpen||el.dataset.reviewId))));renderReviewPagination(total);}
async function refreshReview(){try{const query=new URLSearchParams({limit:String(reviewPageSize),offset:String((reviewPage-1)*reviewPageSize),status:'done',human_review:'true'});if(activeReview!=='all')query.set('review',activeReview);const [images,summaryResponse]=await Promise.all([fetch(`/api/images?${query}`),fetch('/api/images/summary')]);if(!images.ok||!summaryResponse.ok)throw new Error('API request failed');const data=await images.json(),summary=await summaryResponse.json(),total=Number(images.headers.get('X-Total-Count')||data.length),lastPage=Math.max(1,Math.ceil(total/reviewPageSize));if(reviewPage>lastPage){reviewPage=lastPage;return refreshReview();}renderReviewPage(data,total,summary);if(reviewFocusId){const response=await fetch(`/api/images/${encodeURIComponent(reviewFocusId)}`);if(response.ok)detail(await response.json());reviewFocusId=null;}const h=await(await fetch('/api/health')).json();$('#health').textContent=h.model_error?'MODEL ERROR':h.model_loaded?'● SYSTEM READY':'○ MODEL STANDBY';}catch{$('#health').textContent='● OFFLINE';}}

function imageLoaded(image) { return new Promise((resolve,reject)=>{image.onload=()=>resolve(image);image.onerror=reject;}); }
async function detail(row) {
  if(!row)return;
  reviewState.row=row;
  $('#detail-title').textContent=row.filename;
  const detailIndex=detailRows.findIndex(item=>item.id===row.id);
  $('#detail-position').textContent=detailIndex>=0 ? `#${detailIndex+1} / ${detailRows.length} ภาพในรายการนี้` : 'ภาพที่เลือก';
  updateDetailNavigation();
  if(!$('#detail').open) $('#detail').showModal();
  if(isReviewPage) return openReviewEditor(row);
  openPreview(row);
}
function openReviewEditor(row) {
  $('#detail').classList.remove('preview-mode');
  $('#preview-workspace').hidden=true;
  $('#detail-eyebrow').textContent='HUMAN REVIEW / MASK EDITOR';
  $('#detail-summary').textContent='';
  const reviewButton=$('#detail-human-review');
  reviewButton.hidden=row.status!=='done';
  reviewButton.textContent=row.review_requested?'ส่งแล้ว ✓':'ส่งเข้า Human Review';
  reviewButton.disabled=Boolean(row.review_requested);
  reviewButton.onclick=async()=>{
    const result=await queueForReview([row.id],reviewButton);
    if(!result?.updated_ids?.includes(row.id)) return;
    row.review_requested=1; row.review_status='pending'; row.review_requested_at=result.review_requested_at; applyReviewQueueResult([row.id],true,result.review_requested_at); reviewState.row=row;
    reviewButton.textContent='ส่งแล้ว ✓'; reviewButton.disabled=true; setDetailReviewFlow(row); await initialiseCanvas(row);
    showReviewToast('✓ ส่งภาพนี้ไป Human Review แล้ว',[row.id]);
  };
  $('#detail-data').textContent=JSON.stringify({ai_measurement:row.measurement,metadata:row.metadata,error:row.error,review:{status:row.review_status,note:row.reviewer_note,reviewer:row.reviewer_name,reviewed_at:row.reviewed_at,vector_paths:row.review_vector?.length||0}},null,2);
  $('#reviewer-name').value=row.reviewer_name||''; $('#review-note').value=row.reviewer_note||''; $('#review-decision').value=row.review_status==='pending'?'confirmed':row.review_status||'confirmed'; $('#review-save-status').textContent='';
  setDetailReviewFlow(row);
  if(row.status!=='done'){ $('#review-stage').classList.add('review-disabled'); $('#review-save').disabled=true; $('#review-save-status').textContent='รอ AI ประมวลผลภาพนี้ให้เสร็จก่อน'; return; }
  initialiseCanvas(row);
}
function previewStage(row) { return !row.review_requested ? 'unsubmitted' : row.review_status==='pending' ? 'pending' : 'reviewed'; }
function previewMetadata(row) {
  const pixels=pxMeasurement(row.measurement), severity=severityFor(row.measurement), meta=row.metadata||{}, items=[], displayed=new Set();
  const add=(label,value,key=null)=>{if(value==null)return;items.push([label,value]);if(key)displayed.add(key);};
  if(pixels.text) items.push(['ความยาว',pixels.text]);
  items.push(['กว้างสุด',pixels.width == null ? '—' : `<span class="severity-${severity.level}">max ${pixels.width.toFixed(1)} px</span>`]);
  items.push(['เวลาถ่าย',escapeHtml(displayTime(row.captured_at||row.received_at))]);
  if(row.review_requested) items.push(['ส่งเข้าตรวจ',escapeHtml(displayTime(row.review_requested_at))]);
  [['height_m','ความสูงกล้อง (อ้างอิงจุดเริ่มบิน)','m'],['relative_alt_m','ความสูงดิบจาก Pixhawk','m'],['camera_offset_applied_m','offset แนวดิ่งที่หัก','m'],['distance_to_surface_m','ระยะถึงพื้น','m'],['speed_mps','ความเร็ว','m/s'],['roll_deg','Roll','°'],['pitch_deg','Pitch','°'],['yaw_deg','Yaw','°'],['latitude','ละติจูด','°'],['longitude','ลองจิจูด','°'],['battery_voltage_v','แรงดันแบตเตอรี่','V'],['battery_pct','แบตเตอรี่','%'],['focal_length_mm','โฟกัส','mm'],['iso','ISO',''],['capture_interval_s','ช่วงถ่าย','s'],['image_width_px','กว้างภาพ','px'],['image_height_px','สูงภาพ','px'],['camera','กล้อง','']].forEach(([key,label,unit])=>{if(meta[key]!=null){const number=Number(meta[key]);add(label,Number.isFinite(number)&&key!=='camera'?`${number.toFixed(key==='iso'||key.endsWith('_px')?0:1)} ${unit}`.trim():escapeHtml(String(meta[key])),key);}});
  if(meta.height_reference) add('ที่มาความสูง', escapeHtml(String(meta.height_reference)), 'height_reference');
  Object.entries(meta).forEach(([key,value])=>{if(!displayed.has(key)&&['string','number','boolean'].includes(typeof value))add(key,escapeHtml(String(value)),key);});
  if(row.error) items.push(['ข้อผิดพลาด',escapeHtml(row.error)]);
  return items.map(([label,value])=>`<dt>${label}</dt><dd>${value}</dd>`).join('');
}
function openPreview(row) {
  $('#detail').classList.add('preview-mode');
  $('#preview-workspace').hidden=false;
  $('#detail-eyebrow').textContent='รายละเอียดภาพ';
  $('#detail-summary').textContent='ใช้ล้อเมาส์ซูม · ลากเพื่อเลื่อน · ดับเบิลคลิกรีเซ็ต';
  $('#preview-metadata').innerHTML=previewMetadata(row);
  const stage=previewStage(row), chip=$('#preview-status'), result=$('#preview-review-result'), primary=$('#preview-primary'), secondary=$('#preview-secondary'), nextOption=$('#preview-next-option');
  chip.className=`preview-status ${stage}`;
  chip.textContent=stage==='unsubmitted'?'ยังไม่ส่ง':stage==='pending'?'รอคนตรวจ':'ตรวจแล้ว';
  result.hidden=stage!=='reviewed';
  if(stage==='reviewed') result.innerHTML=`<b>${escapeHtml(reviewLabel(row.review_status))}</b><br>ผู้ตรวจ: ${escapeHtml(row.reviewer_name||'—')}<br>เวลา: ${escapeHtml(displayTime(row.reviewed_at))}<br>หมายเหตุ: ${escapeHtml(row.reviewer_note||'—')}`;
  secondary.hidden=true; nextOption.hidden=stage!=='unsubmitted';
  if(stage==='unsubmitted') { primary.textContent='ส่งเข้า Human Review'; primary.onclick=()=>sendPreviewToReview(row); }
  else if(stage==='pending') { primary.textContent='เปิดใน Human Review →'; primary.onclick=()=>window.location.assign(`/review?image=${encodeURIComponent(row.id)}`); secondary.hidden=false; secondary.textContent='ถอนกลับ'; secondary.onclick=()=>undoPreviewReview(row); }
  else { primary.textContent='ดูใน Human Review →'; primary.onclick=()=>window.location.assign(`/review?image=${encodeURIComponent(row.id)}`); }
  loadPreviewImage(row); preloadPreviewNeighbours();
}
async function sendPreviewToReview(row) {
  const button=$('#preview-primary'), result=await queueForReview([row.id],button);
  if(!result?.updated_ids?.includes(row.id)) return;
  row.review_requested=1; row.review_status='pending'; row.review_requested_at=result.review_requested_at; applyReviewQueueResult([row.id],true,result.review_requested_at); reviewState.row=row; openPreview(row); showReviewToast('✓ ส่งภาพนี้ไป Human Review แล้ว',[row.id]);
  if($('#preview-send-next').checked) moveDetail(1);
}
async function undoPreviewReview(row) {
  const button=$('#preview-secondary'); button.disabled=true; button.textContent='กำลังถอนกลับ…';
  try { const response=await fetch('/api/images/review-queue',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({image_ids:[row.id],requested:false})}); if(!response.ok)throw new Error(await response.text()); const result=await response.json(); if(!result.updated_ids?.includes(row.id))throw new Error('ภาพนี้ตรวจแล้ว จึงถอนไม่ได้'); row.review_requested=0; applyReviewQueueResult([row.id],false); reviewState.row=row; openPreview(row); showReviewToast('ถอนกลับแล้ว'); } catch(error) { showReviewToast(`ถอนกลับไม่สำเร็จ: ${error.message}`); button.disabled=false; button.textContent='ถอนกลับ'; }
}
function applyPreviewTransform() { $('#preview-image-layer').style.transform=`translate(${previewState.x}px,${previewState.y}px) scale(${previewState.scale})`; $('#preview-zoom-reset').textContent=`${Math.round(previewState.scale*100)}%`; }
function resetPreviewTransform() { previewState.scale=1; previewState.x=0; previewState.y=0; applyPreviewTransform(); }
function setPreviewOverlay(enabled) { previewState.overlay=enabled; $('#preview-overlay').style.display=enabled&&previewState.hasOverlay?'block':'none'; $('#preview-overlay-toggle').classList.toggle('active',enabled); $('#preview-original-toggle').classList.toggle('active',!enabled); }
function loadPreviewImage(row) { const original=$('#preview-original'), overlay=$('#preview-overlay'); previewState.hasOverlay=Boolean(row.overlay_url); original.src=`${row.original_url}?preview=${Date.now()}`; overlay.src=previewState.hasOverlay?`${row.overlay_url}?preview=${Date.now()}`:''; overlay.onload=()=>setPreviewOverlay(previewState.overlay); resetPreviewTransform(); setPreviewOverlay(previewState.overlay); }
function preloadPreviewNeighbours() { const index=detailRows.findIndex(item=>item.id===reviewState.row?.id); [-1,1].forEach(offset=>{const row=detailRows[index+offset]; if(!row)return; const image=new Image(); image.src=row.overlay_url||row.original_url;}); }
function setupPreviewControls() {
  const stage=$('#preview-stage'), checkbox=$('#preview-send-next');
  try { checkbox.checked=localStorage.getItem('crackstation-preview-next')==='true'; } catch {}
  checkbox.onchange=()=>{try{localStorage.setItem('crackstation-preview-next',String(checkbox.checked));}catch{}};
  $('#preview-original-toggle').onclick=()=>setPreviewOverlay(false); $('#preview-overlay-toggle').onclick=()=>setPreviewOverlay(true);
  $('#preview-zoom-in').onclick=()=>{previewState.scale=Math.min(4,previewState.scale+.25);applyPreviewTransform();}; $('#preview-zoom-out').onclick=()=>{previewState.scale=Math.max(.5,previewState.scale-.25);applyPreviewTransform();}; $('#preview-zoom-reset').onclick=resetPreviewTransform;
  stage.addEventListener('wheel',event=>{if(!$('#detail').open||isReviewPage)return;event.preventDefault();const next=previewState.scale+(event.deltaY<0?.15:-.15);previewState.scale=Math.max(.5,Math.min(4,next));applyPreviewTransform();},{passive:false});
  stage.addEventListener('dblclick',event=>{event.preventDefault();resetPreviewTransform();});
  stage.addEventListener('pointerdown',event=>{if(isReviewPage)return;previewState.drag={x:event.clientX,y:event.clientY,baseX:previewState.x,baseY:previewState.y};stage.classList.add('is-panning');stage.setPointerCapture(event.pointerId);});
  stage.addEventListener('pointermove',event=>{if(!previewState.drag)return;previewState.x=previewState.drag.baseX+event.clientX-previewState.drag.x;previewState.y=previewState.drag.baseY+event.clientY-previewState.drag.y;applyPreviewTransform();});
  ['pointerup','pointercancel','lostpointercapture'].forEach(type=>stage.addEventListener(type,()=>{previewState.drag=null;stage.classList.remove('is-panning');}));
  $('#copy-filename').onclick=async()=>{const button=$('#copy-filename'),name=reviewState.row?.filename||'';try{await navigator.clipboard.writeText(name);button.textContent='คัดลอกแล้ว';setTimeout(()=>button.textContent='คัดลอก',1200);}catch{button.textContent='คัดลอกไม่ได้';}};
}
function setDetailReviewFlow(row) {
  const enabled=row.status==='done' && Boolean(row.review_requested);
  document.querySelectorAll('.review-editor-only').forEach(element=>element.hidden=!enabled);
  $('#review-stage').classList.toggle('review-disabled',!enabled);
  $('#review-save').disabled=!enabled;
  if(!enabled && row.status==='done') $('#review-save-status').textContent='ส่งภาพเข้า Human Review ก่อน จึงจะเริ่มวาด mask และบันทึกผลตรวจได้';
}
function updateDetailNavigation() {
  const index=detailRows.findIndex(row=>row.id===reviewState.row?.id), previous=$('#detail-prev'), next=$('#detail-next');
  previous.disabled=index<=0; next.disabled=index<0||index>=detailRows.length-1;
}
function moveDetail(direction) {
  const index=detailRows.findIndex(row=>row.id===reviewState.row?.id), target=detailRows[index+direction];
  if(target) detail(target);
}
async function initialiseCanvas(row) {
  const original=$('#detail-original'), overlay=$('#detail-overlay'), canvas=$('#review-canvas'), pen=$('#pen-canvas'); reviewState.history=[]; reviewState.last=null; reviewState.paths=normalisePaths(row.review_vector); reviewState.activePath=Math.max(0,reviewState.paths.length-1); reviewState.selected=null;
  const ready=imageLoaded(original); original.src=`${row.original_url}?review=${Date.now()}`; overlay.src=row.overlay_url?`${row.overlay_url}?review=${Date.now()}`:'';
  try { await ready; } catch { $('#review-save-status').textContent='เปิดภาพต้นฉบับไม่ได้'; return; }
  canvas.width=original.naturalWidth; canvas.height=original.naturalHeight; pen.width=original.naturalWidth; pen.height=original.naturalHeight; const ctx=canvas.getContext('2d'); ctx.clearRect(0,0,canvas.width,canvas.height);
  const brushMask=row.reviewer_brush_mask_url||row.review_mask_url;
  if(brushMask){try{await paintStoredMask(brushMask,ctx,canvas);}catch{}} drawPen(); setOverlayOpacity(); setTool('pen');
}
async function paintStoredMask(url,ctx,canvas){ const image=new Image(),ready=imageLoaded(image);image.src=`${url}?review=${Date.now()}`;await ready;const temp=document.createElement('canvas');temp.width=canvas.width;temp.height=canvas.height;const t=temp.getContext('2d');t.drawImage(image,0,0,canvas.width,canvas.height);const pixels=t.getImageData(0,0,canvas.width,canvas.height);for(let i=0;i<pixels.data.length;i+=4){if(pixels.data[i+3]){pixels.data[i]=57;pixels.data[i+1]=220;pixels.data[i+2]=140;pixels.data[i+3]=205;}}ctx.putImageData(pixels,0,0); }
function canvasPoint(event, element=$('#review-canvas')){const r=element.getBoundingClientRect();return{x:(event.clientX-r.left)*element.width/r.width,y:(event.clientY-r.top)*element.height/r.height};}
function snapshot(){const c=$('#review-canvas');reviewState.history.push(c.getContext('2d').getImageData(0,0,c.width,c.height));if(reviewState.history.length>12)reviewState.history.shift();}
function drawSegment(a,b){const c=$('#review-canvas'),ctx=c.getContext('2d');ctx.save();ctx.globalCompositeOperation=reviewState.tool==='erase'?'destination-out':'source-over';ctx.strokeStyle='rgba(57,220,140,.82)';ctx.lineWidth=Number($('#brush-size').value);ctx.lineCap='round';ctx.lineJoin='round';ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.stroke();ctx.restore();}
function normalisePaths(paths){if(!Array.isArray(paths)||!paths.length)return [[]];return paths.map(path=>Array.isArray(path)?path.filter(p=>Number.isFinite(p?.x)&&Number.isFinite(p?.y)).map(p=>({x:p.x,y:p.y,in:p.in||{x:p.x,y:p.y},out:p.out||{x:p.x,y:p.y}})):[]);}
function currentPath(){if(!reviewState.paths[reviewState.activePath])reviewState.paths.push([]),reviewState.activePath=reviewState.paths.length-1;return reviewState.paths[reviewState.activePath];}
function drawPen(){const canvas=$('#pen-canvas'),ctx=canvas.getContext('2d');ctx.clearRect(0,0,canvas.width,canvas.height);ctx.save();ctx.lineCap='round';ctx.lineJoin='round';ctx.lineWidth=Number($('#brush-size').value);ctx.strokeStyle='#47e0a4';reviewState.paths.forEach(path=>{if(!path.length)return;ctx.beginPath();ctx.moveTo(path[0].x,path[0].y);for(let i=1;i<path.length;i++)ctx.bezierCurveTo(path[i-1].out.x,path[i-1].out.y,path[i].in.x,path[i].in.y,path[i].x,path[i].y);if(path.length===1)ctx.lineTo(path[0].x+.01,path[0].y+.01);ctx.stroke();});const selected=reviewState.selected;if(selected){const p=reviewState.paths[selected.path]?.[selected.point];if(p){ctx.lineWidth=1.5;ctx.strokeStyle='#bfffe0';ctx.beginPath();ctx.moveTo(p.x,p.y);ctx.lineTo(p.in.x,p.in.y);ctx.moveTo(p.x,p.y);ctx.lineTo(p.out.x,p.out.y);ctx.stroke();[[p.x,p.y,6,'#ffffff'],[p.in.x,p.in.y,4,'#ffe18c'],[p.out.x,p.out.y,4,'#ffe18c']].forEach(([x,y,r,color])=>{ctx.fillStyle=color;ctx.beginPath();ctx.arc(x,y,r,0,Math.PI*2);ctx.fill();});}}ctx.restore();}
function hitPen(point){const distance=(a,b)=>Math.hypot(a.x-b.x,a.y-b.y);for(let pi=reviewState.paths.length-1;pi>=0;pi--){const path=reviewState.paths[pi];for(let i=path.length-1;i>=0;i--){const p=path[i];if(distance(point,p)<13)return{type:'point',path:pi,point:i};if(reviewState.selected?.path===pi&&reviewState.selected?.point===i){if(distance(point,p.out)<12)return{type:'out',path:pi,point:i};if(distance(point,p.in)<12)return{type:'in',path:pi,point:i};}}}return null;}
function setTool(tool){reviewState.tool=tool;document.querySelectorAll('.mask-tool').forEach(b=>b.classList.toggle('active',b.dataset.tool===tool));const mask=$('#review-canvas'),pen=$('#pen-canvas');mask.style.pointerEvents=tool==='pen'?'none':'auto';pen.style.pointerEvents=tool==='pen'?'auto':'none';mask.classList.toggle('erasing',tool==='erase');$('#review-help').textContent=tool==='pen'?'Pen: คลิกวางจุด · ลากจุดควบคุมสีเหลืองเพื่อปรับส่วนโค้ง · กด ＋ เส้นใหม่สำหรับรอยแตกเส้นถัดไป':tool==='brush'?'แปรง: ลากวาดทับรอยแตกละเอียดบนภาพ':'ยางลบ: ลากลบส่วนของ mask ที่ไม่ต้องการ';drawPen();}
function movePoint(point,dx,dy){point.x+=dx;point.y+=dy;point.in.x+=dx;point.in.y+=dy;point.out.x+=dx;point.out.y+=dy;}
function rasterizePen(ctx){ctx.save();ctx.globalCompositeOperation='source-over';ctx.strokeStyle='white';ctx.lineWidth=Number($('#brush-size').value);ctx.lineCap='round';ctx.lineJoin='round';reviewState.paths.forEach(path=>{if(!path.length)return;ctx.beginPath();ctx.moveTo(path[0].x,path[0].y);for(let i=1;i<path.length;i++)ctx.bezierCurveTo(path[i-1].out.x,path[i-1].out.y,path[i].in.x,path[i].in.y,path[i].x,path[i].y);if(path.length===1)ctx.lineTo(path[0].x+.01,path[0].y+.01);ctx.stroke();});ctx.restore();}
function setOverlayOpacity(){const value=Number($('#overlay-opacity').value);$('#detail-overlay').style.opacity=String(value/100);$('#overlay-value').textContent=`${value}%`;}
function brushMaskDataUrl(){const source=$('#review-canvas'),mask=document.createElement('canvas'),ctx=mask.getContext('2d');mask.width=source.width;mask.height=source.height;const sourcePixels=source.getContext('2d').getImageData(0,0,source.width,source.height),out=ctx.createImageData(source.width,source.height);for(let i=0;i<out.data.length;i+=4){const alpha=sourcePixels.data[i+3];out.data[i]=255;out.data[i+1]=255;out.data[i+2]=255;out.data[i+3]=alpha;}ctx.putImageData(out,0,0);return mask.toDataURL('image/png');}
function maskDataUrl(){const source=$('#review-canvas'),mask=document.createElement('canvas'),ctx=mask.getContext('2d');mask.width=source.width;mask.height=source.height;ctx.drawImage(source,0,0);rasterizePen(ctx);return mask.toDataURL('image/png');}
async function saveReview(){const row=reviewState.row;if(!row||row.status!=='done')return;const button=$('#review-save'),status=$('#review-save-status');button.disabled=true;status.textContent='กำลังบันทึก…';try{const response=await fetch(`/api/images/${row.id}/review`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({decision:$('#review-decision').value,reviewer:$('#reviewer-name').value,note:$('#review-note').value,mask_data:maskDataUrl(),brush_mask_data:brushMaskDataUrl(),vector:reviewState.paths})});if(!response.ok)throw new Error(await response.text());reviewState.row=await response.json();status.textContent='บันทึกผลตรวจแล้ว';await refresh();}catch(error){status.textContent=`บันทึกไม่สำเร็จ: ${error.message}`;}finally{button.disabled=false;}}

async function refresh(){
  if (isReviewPage) return refreshReview();
  const request=++resultsRequest;
  try {
    const query=new URLSearchParams({limit:String(pageSize),offset:String((page-1)*pageSize),review_stage:dashboardReviewFilter});
    if(activeFilter!=='all') query.set('status',activeFilter);
    const [images,summaryResponse,featuredResponse]=await Promise.all([fetch(`/api/images?${query}`),fetch('/api/images/summary'),fetch('/api/overview/latest')]);
    if(!images.ok||!summaryResponse.ok||!featuredResponse.ok) throw new Error('โหลดรายการไม่สำเร็จ กรุณาตรวจว่าเซิร์ฟเวอร์เปิดอยู่');
    const data=await images.json(), summary=await summaryResponse.json(), featured=await featuredResponse.json();
    if(request!==resultsRequest) return;
    if(!['unsubmitted','pending','reviewed','all'].every(key=>Number.isInteger(summary.review?.[key]))) throw new Error('API กับหน้าเว็บคนละรุ่น กรุณา restart เซิร์ฟเวอร์');
    if(data.some(row=>!matchesReviewFilter(row))) throw new Error('เซิร์ฟเวอร์ไม่ได้กรองสถานะ กรุณา restart เซิร์ฟเวอร์');
    if(defaultFilterPending) {
      defaultFilterPending=false;
      if(dashboardReviewFilter==='unsubmitted'&&summary.review.unsubmitted===0) { dashboardReviewFilter='all'; syncReviewUrl(); return refresh(); }
    }
    syncReviewUrl();
    const total=Number(images.headers.get('X-Total-Count')), lastPage=Math.max(1,Math.ceil(total/pageSize));
    if(page>lastPage) { page=lastPage; return refresh(); }
    render(data,total,summary);
    const h=await(await fetch('/api/health')).json();
    if(request!==resultsRequest)return;
    renderOverview(featured,summary,h.session);
    $('#health').textContent=h.model_error?'MODEL ERROR':h.model_loaded?'● SYSTEM READY':'○ MODEL STANDBY';
    $('#session-label').textContent=h.session?`Drone test session · ${h.session}`:'';
  } catch(error) {
    if(request!==resultsRequest)return;
    $('#rows').innerHTML=`<p class="results-empty">${escapeHtml(error.message)}</p>`;
    $('#pagination').innerHTML=''; $('#range-label').textContent='โหลดรายการไม่ได้';
    document.querySelectorAll('.review-filter-tab b').forEach(el=>el.textContent='—');
  }
}
function syncReviewUrl() { const url=new URL(location.href);url.searchParams.set('review',dashboardReviewFilter);history.replaceState(null,'',url); }
function setupMaskEditor(){
  const canvas=$('#review-canvas'), pen=$('#pen-canvas');
  canvas.addEventListener('pointerdown',e=>{if(!reviewState.row||reviewState.row.status!=='done'||reviewState.tool==='pen')return;snapshot();reviewState.drawing=true;reviewState.last=canvasPoint(e,canvas);canvas.setPointerCapture(e.pointerId);drawSegment(reviewState.last,{x:reviewState.last.x+.01,y:reviewState.last.y+.01});});
  canvas.addEventListener('pointermove',e=>{if(!reviewState.drawing)return;const point=canvasPoint(e,canvas);drawSegment(reviewState.last,point);reviewState.last=point;});
  const stopBrush=()=>{reviewState.drawing=false;reviewState.last=null;}; ['pointerup','pointercancel','lostpointercapture'].forEach(type=>canvas.addEventListener(type,stopBrush));
  pen.addEventListener('pointerdown',e=>{if(!reviewState.row||reviewState.row.status!=='done'||reviewState.tool!=='pen')return;const point=canvasPoint(e,pen),hit=hitPen(point);pen.setPointerCapture(e.pointerId);if(hit){reviewState.selected={path:hit.path,point:hit.point};reviewState.penDrag={...hit,last:point};}else{const path=currentPath(),node={x:point.x,y:point.y,in:{x:point.x,y:point.y},out:{x:point.x,y:point.y}};path.push(node);reviewState.selected={path:reviewState.activePath,point:path.length-1};reviewState.penDrag={type:'new',path:reviewState.activePath,point:path.length-1,last:point};}drawPen();});
  pen.addEventListener('pointermove',e=>{const drag=reviewState.penDrag;if(!drag)return;const point=canvasPoint(e,pen),node=reviewState.paths[drag.path]?.[drag.point];if(!node)return;if(drag.type==='point'){movePoint(node,point.x-drag.last.x,point.y-drag.last.y);}else if(drag.type==='in'||drag.type==='out'||drag.type==='new'){const which=drag.type==='in'?'in':'out';node[which]={x:point.x,y:point.y};const other=which==='in'?'out':'in';node[other]={x:2*node.x-point.x,y:2*node.y-point.y};}drag.last=point;drawPen();});
  const stopPen=()=>{reviewState.penDrag=null;};['pointerup','pointercancel','lostpointercapture'].forEach(type=>pen.addEventListener(type,stopPen));
  document.querySelectorAll('.mask-tool').forEach(b=>b.onclick=()=>setTool(b.dataset.tool));
  $('#pen-new-path').onclick=()=>{reviewState.paths.push([]);reviewState.activePath=reviewState.paths.length-1;reviewState.selected=null;setTool('pen');};
  $('#brush-size').oninput=e=>{$('#brush-value').textContent=`${e.target.value} px`;drawPen();};$('#overlay-opacity').oninput=setOverlayOpacity;
  $('#mask-undo').onclick=()=>{const path=currentPath();if(reviewState.tool==='pen'&&path.length){path.pop();reviewState.selected=null;drawPen();return;}const image=reviewState.history.pop();if(image){const c=$('#review-canvas');c.getContext('2d').putImageData(image,0,0);}};
  $('#mask-clear').onclick=()=>{reviewState.paths=[[]];reviewState.activePath=0;reviewState.selected=null;snapshot();const c=$('#review-canvas');c.getContext('2d').clearRect(0,0,c.width,c.height);drawPen();};$('#review-save').onclick=saveReview;
}

async function setup(){
  $('#dashboard-view').hidden=isReviewPage;
  $('#review-view').hidden=!isReviewPage;
  $('#control-nav').classList.toggle('active',!isReviewPage);
  $('#human-review-nav').classList.toggle('active',isReviewPage);
  $('#sidebar-control').classList.toggle('active',!isReviewPage);
  $('#sidebar-review').classList.toggle('active',isReviewPage);
  setupMaskEditor();
  setupPreviewControls();
  const floatingObserver=new ResizeObserver(entries=>entries.forEach(entry=>document.body.style.setProperty(entry.target.id==='review-toast'?'--review-toast-height':'--selection-bar-height',`${entry.target.getBoundingClientRect().height}px`)));
  floatingObserver.observe($('#review-toast')); floatingObserver.observe($('#selection-action-bar'));

  if (!isReviewPage) {
    const s=await(await fetch('/api/settings')).json(),liveImage=$('#live-image'),liveEmpty=$('#live-empty');
    configureSeverity(s.card_severity);
    liveImage.onload=()=>{liveImage.dataset.available='true';if(overviewMode==='live'){liveImage.hidden=false;liveEmpty.hidden=true;}};
    liveImage.onerror=()=>{liveImage.dataset.available='false';liveImage.hidden=true;if(overviewMode==='live')liveEmpty.hidden=false;liveEmpty.querySelector('p').textContent='ยังรับภาพ live stream ไม่ได้';};
    if(s.live_stream_url)liveImage.src=s.live_stream_url;
    $('#show-ai').onclick=()=>setOverviewMode('ai');$('#show-live').onclick=()=>setOverviewMode('live');
    $('#featured-open').onclick=()=>{if(!featuredRow)return;if(featuredRow.historical)window.open(featuredRow.original_url,'_blank','noopener');else detail(featuredRow);};
    document.querySelectorAll('.filter-tab').forEach(b=>b.onclick=()=>{activeFilter=b.dataset.filter;page=1;document.querySelectorAll('.filter-tab').forEach(x=>x.classList.toggle('active',x===b));refresh();});
    document.querySelectorAll('.review-filter-tab').forEach(button=>button.onclick=()=>{dashboardReviewFilter=button.dataset.reviewFilter;page=1;const url=new URL(window.location.href);url.searchParams.set('review',dashboardReviewFilter);history.replaceState(null,'',url);refresh();});
    $('#page-size').onchange=e=>{pageSize=Number(e.target.value);page=1;refresh();};
    $('#select-page').onclick=selectCurrentPage;
    $('#clear-selection').onclick=clearSelection;
    $('#selection-bar-clear').onclick=clearSelection;
    $('#selection-bar-send').onclick=()=>setSelectionConfirmation(true);
    $('#selection-confirm-cancel').onclick=()=>setSelectionConfirmation(false);
    $('#selection-confirm-submit').onclick=submitSelectedForReview;
    $('#review-toast-undo').onclick=undoReviewQueue;
    $('#review-toast-retry').onclick=submitSelectedForReview;
    $('#rows').addEventListener('keydown',event=>{
      if ((event.metaKey||event.ctrlKey) && event.key.toLowerCase()==='a' && event.target.closest('.dashboard-card')) { event.preventDefault(); selectCurrentPage(); }
    });
    setupResizablePanels();
  } else {
    document.querySelectorAll('.review-tab').forEach(b=>b.onclick=()=>{activeReview=b.dataset.review;reviewPage=1;document.querySelectorAll('.review-tab').forEach(x=>x.classList.toggle('active',x===b));refreshReview();});
    $('#review-page-size').onchange=e=>{reviewPageSize=Number(e.target.value);reviewPage=1;refreshReview();};
  }
  refresh();
  setInterval(refresh,3000);
  setInterval(()=>{$('#clock').textContent=new Date().toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit'});},1000);
}
function setupResizablePanels(){const grid=$('.console'),handle=$('#layout-resizer'),reset=$('#layout-reset'),defaultWidth=66,clamp=value=>Math.max(45,Math.min(82,((grid.clientWidth-300-handle.offsetWidth-24)/grid.clientWidth)*100,Number(value)||defaultWidth)),apply=value=>{const width=clamp(value);grid.style.setProperty('--live-width',`${width}%`);handle.setAttribute('aria-valuenow',String(Math.round(width)));try{localStorage.setItem('crackstation-live-width',String(width));}catch{}};let saved=defaultWidth;try{saved=Number(localStorage.getItem('crackstation-live-width'))||defaultWidth;}catch{}apply(saved);let dragging=false;handle.addEventListener('pointerdown',e=>{if(innerWidth<=980)return;dragging=true;handle.setPointerCapture(e.pointerId);document.body.classList.add('is-resizing');e.preventDefault();});handle.addEventListener('pointermove',e=>{if(dragging){const r=grid.getBoundingClientRect();apply(((e.clientX-r.left)/r.width)*100);}});const stop=()=>{dragging=false;document.body.classList.remove('is-resizing');};['pointerup','pointercancel','lostpointercapture'].forEach(type=>handle.addEventListener(type,stop));window.addEventListener('pointerup',stop,true);window.addEventListener('blur',stop);handle.addEventListener('dblclick',()=>apply(defaultWidth));handle.addEventListener('keydown',e=>{if(['ArrowLeft','ArrowRight'].includes(e.key)){e.preventDefault();apply(Number(handle.getAttribute('aria-valuenow'))+(e.key==='ArrowLeft'?-2:2));}});reset.onclick=()=>apply(defaultWidth);window.addEventListener('resize',()=>apply(Number(handle.getAttribute('aria-valuenow'))));}
$('#close').onclick=()=>$('#detail').close();
$('#detail-prev').onclick=()=>moveDetail(-1);
$('#detail-next').onclick=()=>moveDetail(1);
document.addEventListener('keydown',event=>{
  if(selectionConfirming && !selectionSending) {
    if(event.key==='Escape'){event.preventDefault();setSelectionConfirmation(false);return;}
    if(event.key==='Enter'){event.preventDefault();submitSelectedForReview();return;}
  }
  const dialog=$('#detail');
  if(!dialog.open || /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName||'')) return;
  if(!isReviewPage && event.key==='Enter' && previewStage(reviewState.row)==='unsubmitted'){event.preventDefault();$('#preview-primary').click();return;}
  if(event.key==='ArrowLeft'){event.preventDefault();moveDetail(-1);}
  if(event.key==='ArrowRight'){event.preventDefault();moveDetail(1);}
});
setup();
