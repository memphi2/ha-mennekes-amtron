const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.join(__dirname,'../custom_components/mennekes_amtron_rtu/device');
const source=fs.readFileSync(path.join(root,'app_charging.js'),'utf8');
const initial=()=>({schema:1,csrf:'fixture-csrf',revision:'r1',read_only:false,energy:{target:10,status:'active'},soc:{configured:true,target:80,maximum:90,maximum_required:false,status:'active'},departure:{configured:true,revision:'d1',deadline:'2026-11-16T07:30:45.000Z',status:'paused_until_start',planned_start:null,forecast:null,storage_error:false}});
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function setup(page='targets',responses=[initial()],language='de'){
 const html=fs.readFileSync(path.join(root,page==='targets'?'app_targets.html':'app_departure.html'),'utf8');
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
test('read-only, missing profile and declined confirmation send nothing',async()=>{
 for(const data of [{...initial(),read_only:true},{...initial(),soc:{configured:false}}]){
  const h=setup('targets',[data]);await flush();assert.equal(h.elements.get('soc_save').disabled,true);await h.emit('soc_form','submit');assert.equal(h.calls.length,1);
 }
 const h=setup();await flush();h.cancel();await h.emit('energy_form','submit');assert.equal(h.calls.length,1);
});
test('unchanged displayed departure keeps the exact saved instant including seconds',async()=>{
 const h=setup('departure',[initial(),initial()]);await flush();await h.emit('departure_form','submit');
 assert.equal(JSON.parse(h.calls[1].body).value,initial().departure.deadline);
 assert.equal(JSON.parse(h.calls[1].body).revision,'d1');
});
test('clear affects only departure and explicitly confirms it',async()=>{
 const next=initial();next.departure={...next.departure,revision:'d2',deadline:null};
 const h=setup('departure',[initial(),next]);await flush();await h.emit('departure_clear','click');
 assert.deepEqual(JSON.parse(h.calls[1].body),{action:'departure',value:null,revision:'d1',confirm:true});assert.equal(h.elements.get('departure_value').value,'');assert.match(h.elements.get('departure_summary').textContent,/80/);
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
