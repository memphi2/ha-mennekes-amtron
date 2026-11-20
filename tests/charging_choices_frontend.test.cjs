const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.join(__dirname,'../custom_components/mennekes_amtron_rtu/device');
const source=fs.readFileSync(path.join(root,'app_charging.js'),'utf8');
const initial=()=>({schema:1,csrf:'fixture-csrf',revision:'r1',read_only:false,guest_revision:'g1',guest_active:false,guest_mode:false,energy:{target:10,status:'active'},cost:null,cost_price:0.3,cost_price_at:'2026-11-16T05:00:00+00:00',guest_confirmation_required:false,soc:{configured:true,target:80,maximum:90,maximum_required:false,status:'active'},departure:{configured:true,revision:'d1',deadline:'2026-11-16T07:30:45.000Z',status:'paused_until_start',planned_start:null,storage_error:false}});
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function setup(page='targets',responses=[initial()],language='de'){
 const html=fs.readFileSync(path.join(root,'app_targets.html'),'utf8');
 const elements=new Map([...html.matchAll(/id="([^"]+)"/g)].map(m=>[m[1],{value:'',textContent:'',disabled:false,listeners:{},addEventListener(k,v){this.listeners[k]=v;},reportValidity(){return true;}}]));
 const timers=new Map(),calls=[],events={},docEvents={};let next=1,confirmed=true;
 const document={hidden:false,documentElement:{lang:language,classList:{add(){}}},querySelectorAll(){return [];},querySelector(){return {dataset:{chargingPage:page}};},getElementById(id){assert.ok(elements.has(id),id);return elements.get(id);},addEventListener(k,v){docEvents[k]=v;}};
 const context={document,window:{confirm(){return confirmed;}},Date,AbortController,setTimeout(fn,ms){const id=next++;timers.set(id,{ms,fn:()=>{timers.delete(id);return fn();}});return id;},clearTimeout(id){timers.delete(id);},addEventListener(k,v){events[k]=v;},async fetch(url,options){calls.push({url,...options});let r=responses.shift();if(typeof r==='function')r=await r();if(r instanceof Error)throw r;assert.ok(r,'unexpected request');return {ok:!r.error,async json(){return r;}};}};
 vm.runInNewContext(source,context);
 return {elements,timers,calls,responses,events,docEvents,document,cancel(){confirmed=false;},async emit(id,event){elements.get(id).listeners[event]({preventDefault(){}});await flush();},async poll(){await [...timers.values()].find(t=>t.ms===5000).fn();await flush();}};
}
test('saving a target names only the intended field and current revision',async()=>{
 const next=initial();next.revision='r2';next.energy.target=20;
 const h=setup('targets',[initial(),next]);await flush();h.elements.get('energy_value').value='20';await h.emit('energy_form','input');await h.emit('energy_form','submit');
 assert.deepEqual(JSON.parse(h.calls[1].body),{action:'energy',value:20,revision:'r1',confirm:true});
 assert.equal(h.elements.get('soc_value').value,'80');assert.equal(h.elements.get('notice').textContent,'Gespeichert');
 assert.equal(h.calls.filter(c=>c.method==='POST').length,1);
 assert.equal([...h.timers.values()].filter(t=>t.ms===5000).length,1);
});
test('polls keep unsaved input and its original revision, rejecting stale intent',async()=>{
 const next=initial();next.revision='r2';next.soc.target=60;
 const h=setup('targets',[initial(),next,{error:'soc_goal_source_changed'}]);await flush();h.elements.get('soc_value').value='75';await h.emit('soc_form','input');await h.poll();
 assert.equal(h.elements.get('soc_value').value,'75');assert.match(h.elements.get('notice').textContent,/andernorts geändert/);
 await h.emit('soc_form','submit');assert.equal(JSON.parse(h.calls.at(-1).body).revision,'r1');assert.equal(h.elements.get('soc_value').value,'75');
});
test('read-only and missing profile send nothing',async()=>{
 for(const data of [{...initial(),read_only:true},{...initial(),soc:{configured:false}}]){
  const h=setup('targets',[data]);await flush();assert.equal(h.elements.get('soc_save').disabled,true);await h.emit('soc_form','submit');assert.equal(h.calls.length,1);
 }
});
test('unchanged displayed departure keeps the exact saved instant including seconds',async()=>{
 const h=setup('departure',[initial(),initial()]);await flush();await h.emit('departure_form','submit');
 assert.equal(JSON.parse(h.calls[1].body).value,initial().departure.deadline);
 assert.equal(JSON.parse(h.calls[1].body).revision,'d1');
});
test('clear affects only departure and explicitly confirms it',async()=>{
 const next=initial();next.departure={...next.departure,revision:'d2',deadline:null};
 const h=setup('departure',[initial(),next]);await flush();await h.emit('departure_clear','click');
 assert.deepEqual(JSON.parse(h.calls[1].body),{action:'departure',value:null,revision:'d1',confirm:true});assert.equal(h.elements.get('departure_value').value,'');assert.equal(h.elements.get('soc_value').value,'80');
});
test('lost response remains uncertain and does not retry the write',async()=>{
 const h=setup('targets',[initial(),new Error('lost'),initial()]);await flush();await h.emit('energy_form','submit');assert.match(h.elements.get('notice').textContent,/nicht bestätigt/);await h.poll();assert.equal(h.calls.filter(c=>c.method==='POST').length,1);
});
test('visibility change discards late reads and keeps a single polling timer',async()=>{
 let resolve;const h=setup('targets',[()=>new Promise(r=>{resolve=r;}),initial()]);await flush();h.document.hidden=true;h.docEvents.visibilitychange();resolve(initial());await flush();assert.equal(h.elements.get('energy_save').disabled,true);assert.equal(h.timers.size,0);
 h.document.hidden=false;h.docEvents.visibilitychange();await flush();assert.equal(h.elements.get('energy_save').disabled,false);assert.equal([...h.timers.values()].filter(t=>t.ms===5000).length,1);
});
test('all page languages label valid planning states rather than showing raw keys',async()=>{
 for(const lang of ['en','de','fr','nl','it']){const h=setup('departure',[initial()],lang);await flush();assert.notEqual(h.elements.get('departure_status').textContent,'paused_until_start');assert.ok(h.elements.get('departure_status').textContent.length);}
});
test('invalid or nonexistent local departure never posts a normalized time',async()=>{
 const previous=process.env.TZ;process.env.TZ='Europe/Berlin';
 try{for(const value of ['', 'not-a-time', '2027-03-28T02:30']){
  const h=setup('departure');await flush();h.elements.get('departure_value').value=value;
  await h.emit('departure_form','input');await h.emit('departure_form','submit');assert.equal(h.calls.length,1);
 }}finally{if(previous===undefined)delete process.env.TZ;else process.env.TZ=previous;}
});
test('malformed successful save is uncertain and preserves unsaved input',async()=>{
 const h=setup('targets',[initial(),{schema:1},initial()]);await flush();h.elements.get('energy_value').value='25';await h.emit('energy_form','input');await h.emit('energy_form','submit');
 assert.match(h.elements.get('notice').textContent,/nicht bestätigt/);await h.poll();assert.equal(h.elements.get('energy_value').value,'25');assert.equal(h.calls.filter(c=>c.method==='POST').length,1);
});

test('guest is a single explicit command using the shared selection revision',async()=>{
 const h=setup('targets',[initial(),initial()]);await flush();await h.emit('guest_start','click');
 assert.deepEqual(JSON.parse(h.calls[1].body),{action:'guest',value:true,revision:'g1',confirm:true});
 assert.equal(h.calls.filter(c=>c.method==='POST').length,1);
});
test('unsaved edits block guest start rather than using unexpected limits',async()=>{
 const h=setup();await flush();h.elements.get('energy_value').value='25';await h.emit('energy_form','input');
 assert.equal(h.elements.get('guest_start').disabled,true);await h.emit('guest_start','click');assert.equal(h.calls.length,1);
});
test('active guest button requests the ordinary pause',async()=>{
 const h=setup('targets',[{...initial(),guest_active:true},initial()]);await flush();
 assert.equal(h.elements.get('guest_start').disabled,false);
 assert.equal(h.elements.get('guest_start').textContent,'Gastladen pausieren');
 await h.emit('guest_start','click');assert.equal(h.calls.length,2);
 assert.equal(JSON.parse(h.calls[1].body).action,'pause');
});

test('unsaved limits do not block pausing an active guest',async()=>{
 const h=setup('targets',[{...initial(),guest_active:true},initial()]);await flush();
 h.elements.get('energy_value').value='25';await h.emit('energy_form','input');
 assert.equal(h.elements.get('guest_start').disabled,false);
 await h.emit('guest_start','click');
 assert.equal(JSON.parse(h.calls[1].body).action,'pause');
 assert.equal(h.elements.get('energy_value').value,'25');
});
test('a definite refusal names the fix; anything else stays uncertain',async()=>{
 for(const [code,text] of [['guest_start_not_permitted',/Fahrzeug anstecken/],['guest_deadline_expired',/Zielzeitpunkt ist vorbei/],['energy_goal_stop_active',/Ladeziel ist bereits erreicht/],['charging_save_uncertain',/nicht bestätigt/],['toString',/nicht bestätigt/]]){
  const h=setup('targets',[initial(),{error:code}]);await flush();await h.emit('guest_start','click');
  assert.match(h.elements.get('notice').textContent,text,code);
 }
});
test('an incomplete departure time asks for a full date instead of setup',async()=>{
 const h=setup('departure',[initial()]);await flush();h.elements.get('departure_value').value='2026-11-16';await h.emit('departure_form','submit');
 assert.equal(h.calls.length,1);assert.match(h.elements.get('notice').textContent,/Vollständiges Datum/);
});
test('read-only targets say read only instead of asking for setup',async()=>{
 const h=setup('targets',[{...initial(),read_only:true}]);await flush();
 assert.equal(h.elements.get('energy_status').textContent,'Nur lesen');
});

test('a cost ceiling is saved in euros and shown with what it used',async()=>{
 const next=initial();next.energy.target=33.3;next.cost={amount:10,spent:1.5,price:0.2874};
 const h=setup('targets',[initial(),next],'en');await flush();
 assert.equal(h.elements.get('cost_value').disabled,false);
 h.elements.get('cost_value').value='10';await h.emit('cost_form','input');await h.emit('cost_form','submit');
 assert.deepEqual(JSON.parse(h.calls[1].body),{action:'cost',value:10,revision:'r1',confirm:true});
 assert.equal(h.elements.get('cost_value').value,'10');
 assert.match(h.elements.get('cost_status').textContent,/€10\.00 · €1\.50 used · €0\.2874\/kWh/);
 assert.match(h.elements.get('energy_status').textContent,/from the cost target/);
});
test('without a price the cost ceiling waits for Home Assistant',async()=>{
 const state=initial();state.cost_price=null;
 const h=setup('targets',[state],'en');await flush();
 assert.equal(h.elements.get('cost_value').disabled,true);
 assert.match(h.elements.get('cost_status').textContent,/configure charging costs/);
});
test('a waiting stop refusal, a past time and a restarted guest are named',async()=>{
 const restarted=initial();restarted.guest_confirmation_required=true;
 const h=setup('targets',[restarted,{error:'write_rate_limited'},initial(),{error:'target_time_not_future'}],'en');await flush();
 assert.match(h.elements.get('notice').textContent,/interrupted/);
 await h.emit('guest_start','click');
 assert.match(h.elements.get('notice').textContent,/waiting stop/);
 await h.emit('reload','click');
 h.elements.get('departure_value').value='2026-11-17T08:00';await h.emit('departure_form','input');await h.emit('departure_form','submit');
 assert.match(h.elements.get('notice').textContent,/target time has passed/);
 assert.match(h.elements.get('departure_status').textContent,/Paused until start/);
});

test('an own save keeps another unsaved field current instead of calling it changed elsewhere',async()=>{
 const next=initial();next.revision='r2';next.cost={amount:10,spent:0,price:0.3};next.energy.target=33.3;
 const after=initial();after.revision='r3';after.energy.target=20;
 const h=setup('targets',[initial(),next,after],'en');await flush();
 h.elements.get('energy_value').value='20';await h.emit('energy_form','input');
 h.elements.get('cost_value').value='10';await h.emit('cost_form','input');await h.emit('cost_form','submit');
 assert.equal(h.elements.get('notice').textContent,'Saved');assert.equal(h.elements.get('energy_value').value,'20');
 await h.emit('energy_form','submit');
 assert.equal(JSON.parse(h.calls[2].body).revision,'r2');
});
test('the departure plan names its planned start, and a plan that could not be saved',async()=>{
 const planned=initial();planned.departure.planned_start='2026-11-16T03:00:00.000Z';
 let h=setup('departure',[planned],'en');await flush();
 assert.match(h.elements.get('departure_summary').textContent,/^Planned start: .*26/);
 h=setup('departure',[initial()],'en');await flush();
 assert.equal(h.elements.get('departure_summary').textContent,'');
 const broken=initial();broken.departure.planned_start='soon';
 h=setup('departure',[broken],'en');await flush();
 assert.equal(h.elements.get('departure_summary').textContent,'');
 broken.departure.storage_error=true;
 h=setup('departure',[broken],'de');await flush();
 assert.match(h.elements.get('departure_summary').textContent,/nicht gespeichert/);
});
test('without an amount the page names the price and since when Home Assistant reported it',async()=>{
 let h=setup('targets',[initial()],'en');await flush();
 assert.match(h.elements.get('cost_status').textContent,/^Disabled · €0\.30\/kWh \(reported by Home Assistant .*26.*\)$/);
 const bare=initial();bare.cost_price_at=null;
 h=setup('targets',[bare],'en');await flush();
 assert.equal(h.elements.get('cost_status').textContent,'Disabled · €0.30/kWh');
});
test('a guest or a failed plan shows no planned start, and broken plan fields are refused',async()=>{
 const guest=initial();guest.departure.planned_start='2026-11-16T03:00:00.000Z';guest.departure.status='guest_mode';
 let h=setup('departure',[guest],'en');await flush();
 assert.equal(h.elements.get('departure_summary').textContent,'');
 for(const change of [{planned_start:12345},{storage_error:'false'}]){
  const broken=initial();Object.assign(broken.departure,change);
  h=setup('departure',[broken],'en');await flush();
  assert.match(h.elements.get('notice').textContent,/No current response/);
 }
});

test('every target is cleared with its own button, and only a set one',async()=>{
 const cost=initial();cost.cost={amount:10,spent:0,price:0.3};
 for(const [key,body] of [['energy',{action:'energy',value:0,revision:'r1'}],['cost',{action:'cost',value:0,revision:'r1'}],['soc',{action:'soc',value:0,revision:'r1'}],['departure',{action:'departure',value:null,revision:'d1'}]]){
  const h=setup('targets',[cost,initial()]);await flush();
  assert.equal(h.elements.get(key+'_clear').disabled,false,key);
  await h.emit(key+'_clear','click');
  assert.deepEqual(JSON.parse(h.calls[1].body),{...body,confirm:true});
 }
 const empty=initial();empty.energy.target=0;empty.soc.target=0;empty.departure.deadline=null;
 const h=setup('targets',[empty]);await flush();
 for(const key of ['energy','cost','soc','departure'])assert.equal(h.elements.get(key+'_clear').disabled,true,key);
});
test('the guest card names what ends a guest charge, and the vehicle target is not part of it',async()=>{
 let h=setup('targets',[initial()]);await flush();
 assert.match(h.elements.get('guest_summary').textContent,/^Endet bei: 10 kWh · 16\.11\.26, /);
 assert.doesNotMatch(h.elements.get('guest_summary').textContent,/80/);
 assert.equal(h.elements.get('guest_status').textContent,'');
 assert.equal(h.elements.get('soc_status').textContent,'80 % · Aktiv');
 const cost=initial();cost.cost={amount:10,spent:0,price:0.3};cost.energy.target=33.3;cost.departure=null;
 h=setup('targets',[cost]);await flush();
 assert.match(h.elements.get('guest_summary').textContent,/^Endet bei: 10,00\s€ \(33\.3 kWh\)$/);
 const open=initial();open.energy.target=0;open.departure.deadline=null;
 h=setup('targets',[open]);await flush();
 assert.match(h.elements.get('guest_summary').textContent,/^Kein Ziel gesetzt/);
});
test('while a guest holds the wallbox the vehicle target says that it does not apply',async()=>{
 const guest={...initial(),guest_active:true,guest_mode:true};guest.departure.status='guest_mode';
 let h=setup('targets',[guest]);await flush();
 assert.equal(h.elements.get('guest_status').textContent,'Gastladen läuft');
 assert.equal(h.elements.get('soc_status').textContent,'80 % · gilt nicht für den Gast');
 assert.match(h.elements.get('departure_status').textContent,/Beendet das Gastladen$/);
 h=setup('targets',[{...guest,guest_active:false}]);await flush();
 assert.equal(h.elements.get('guest_status').textContent,'Gastladen ist pausiert');
 assert.equal(h.elements.get('guest_start').textContent,'Gastladen starten');
});
