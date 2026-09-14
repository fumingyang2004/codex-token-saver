const $ = id => document.getElementById(id);
const token = new URLSearchParams(location.search).get('token') || '';
let chosen = '', busy = false;
const num = n => n === null || n === undefined ? '—' : n.toLocaleString('en-US');
const short = n => n === null || n === undefined ? '—' : n >= 10000 ? (n/1000).toFixed(1)+'K' : num(n);
async function api(path) { const r = await fetch(path,{headers:{'X-Saver-Token':token},cache:'no-store'}); if(!r.ok) throw Error('Unavailable'); return r.json(); }
function render(value) {
  const {session,summary,sessions} = value;
  const old = $('sessions').value;
  const options = [new Option('Current session',''),...sessions.map(s=>new Option((s.active?'Active · ':'Previous · ')+s.id.slice(0,8),s.id))];
  $('sessions').replaceChildren(...options); $('sessions').value = old;
  $('sessionLabel').textContent = session ? (session.active?'Session: Active':'Previous session')+' · '+session.id.slice(0,8) : 'No active Codex session';
  $('activity').classList.toggle('active',!!session?.active);
  $('total').textContent = summary ? num(summary.observed_net_avoided_tokens) : '—';
  $('subtitle').textContent = session ? 'Recorded reductions, net of measured overhead.' : 'Start Codex normally. Savings will appear here automatically.';
  for (const c of ['rtk','cce']) {
    const item = summary?.components[c];
    $(c).textContent = item ? num(item.saved) : '—';
    $(c+'Events').textContent = item?.events ? item.events+' observed event'+(item.events===1?'':'s') : 'No observations yet';
    // Visual proportion only; displayed accounting values always come from API.
    const other = summary?.components[c==='rtk'?'cce':'rtk']?.saved || 0;
    $(c+'Bar').style.width = item?.saved > 0 ? Math.max(0,item.saved)/(Math.max(0,item.saved)+Math.max(0,other))*100+'%' : '0%';
  }
  const usage = summary?.actual_usage;
  $('input').textContent=short(usage?.input_tokens); $('cached').textContent=short(usage?.cached_input_tokens); $('output').textContent=short(usage?.output_tokens);
  $('identity').textContent=session ? 'Session: '+session.id : '';
  $('reasoning').textContent='Reasoning output: '+num(usage?.reasoning_output_tokens)+' (included in output).';
  $('warnings').textContent=(summary?.warnings||[]).join(' ');
  const events=[...(summary?.events||[])].sort((a,b)=>Date.parse(typeof b.timestamp==='number'?new Date(b.timestamp*1000):b.timestamp)-Date.parse(typeof a.timestamp==='number'?new Date(a.timestamp*1000):a.timestamp)).slice(0,30);
  const rows=events.map(e=>{
    const row=document.createElement('div'); row.className='event'; row.title=e.reason;
    const date=new Date(typeof e.timestamp==='number'?e.timestamp*1000:e.timestamp);
    for (const [tag,text,cls] of [['time',date.toLocaleTimeString([], {hour12:false}),''],['b',e.component.toUpperCase(),''],['span',num(e.before_tokens)+' → '+num(e.after_tokens),'change'],['span',(e.delta_tokens>0?'+':'')+num(e.delta_tokens),e.delta_tokens<0?'gain cost':'gain']]) {
      const el=document.createElement(tag);el.textContent=text;el.className=cls;row.appendChild(el);
    }
    return row;
  });
  if(!rows.length){const p=document.createElement('p');p.className='empty';p.textContent=session?'No measured transformations yet. Unavailable does not mean zero saved.':'Start Codex normally. Observed reductions will appear here automatically.';rows.push(p);}
  $('events').replaceChildren(...rows);
}
async function refresh(){if(busy)return;busy=true;try{const [status,value]=await Promise.all([api('/api/status'),api('/api/session/current'+(chosen?'?session='+encodeURIComponent(chosen):''))]);$('mode').textContent='Efficiency '+status.status;render(value);$('live').textContent='Live · updated '+new Date().toLocaleTimeString();}catch{$('live').textContent='Connection unavailable · retrying';}finally{busy=false;}}
$('sessions').addEventListener('change',()=>{chosen=$('sessions').value;refresh();});
refresh();setInterval(refresh,1000);
