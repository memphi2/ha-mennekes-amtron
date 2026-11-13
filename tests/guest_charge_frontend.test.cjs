const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '../custom_components/mennekes_amtron_rtu/device');
const html = fs.readFileSync(path.join(root, 'app_guest.html'), 'utf8');
const source = fs.readFileSync(path.join(root, 'app_guest.js'), 'utf8');
const initial = () => ({schema:1,revision:'r1',scope:'a'.repeat(64),csrf:'test-csrf',ready:true,available:true,present:true,permitted:true,meter_fresh:true,max_current_a:16,vehicle_target_active:false,job:null});
const proposal = () => ({schema:1,revision:'r1',scope:'a'.repeat(64),request:{id:'b'.repeat(32),kind:'guest',start_at:'2026-11-13T18:00:00Z',finish_at:'2026-11-13T20:00:00Z',current_a:6,energy_kwh:15}});
const flush = () => new Promise(resolve => setImmediate(resolve));
function setup(responses=[initial()], language='de') {
  const elements = new Map([...html.matchAll(/id="([^"]+)"/g)].map(m=>[m[1],{value:'',textContent:'',disabled:false,hidden:false,listeners:{},addEventListener(k,fn){this.listeners[k]=fn;},reportValidity(){return true;}}]));
  elements.get('guest_energy').value='15';elements.get('guest_duration').value='120';elements.get('guest_current').value='6';
  const calls=[], timers=new Map(), events={}, docEvents={};let next=1;
  const document={hidden:false,documentElement:{lang:language},querySelectorAll(){return [];},getElementById(id){assert.ok(elements.has(id),id);return elements.get(id);},addEventListener(k,fn){docEvents[k]=fn;}};
  const context={document,AbortController,Date,crypto:{randomUUID(){return 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb';}},setTimeout(fn,ms){const id=next++;timers.set(id,{fn:()=>{timers.delete(id);return fn();},ms});return id;},clearTimeout(id){timers.delete(id);},addEventListener(k,fn){events[k]=fn;},async fetch(url,opts){calls.push({url,...opts});let r=responses.shift();if(typeof r==='function')r=await r();if(r instanceof Error)throw r;assert.notEqual(r,undefined,'unexpected request');return {ok:!r.error,status:r.error?409:200,async json(){return r;}};}};
  vm.runInNewContext(source,context,{filename:'app_guest.js'});
  return {elements,calls,timers,events,docEvents,document,responses,async emit(id,event){await elements.get(id).listeners[event]({preventDefault(){}});await flush();}};
}

test('explicit preview precedes a fixed absolute create; no retrying timer',async()=>{
  const h=setup([initial(),proposal(),{schema:1}, {...initial(),revision:'r2',job:{...proposal().request,state:'charging',delivered_kwh:0,holding:true}}]);await flush();
  assert.equal(h.elements.get('guest_fields').disabled,false);
  await h.emit('guest_form','submit');
  let post=JSON.parse(h.calls[1].body);assert.equal(post.action,'preview_guest');assert.equal(post.request.duration_minutes,120);
  assert.equal(h.elements.get('guest_confirmation').hidden,false);
  await h.emit('guest_confirm','click');
  post=JSON.parse(h.calls[2].body);assert.equal(post.action,'create');assert.equal(post.request.start_at,proposal().request.start_at);
  assert.equal(post.request.kind,'guest');assert.equal(h.calls[2].headers['X-Amtron-CSRF'],'test-csrf');
  assert.equal(h.calls.filter(c=>c.method==='POST').length,2);
  assert.equal(h.elements.get('guest_fields').disabled,true);
  assert.equal(h.elements.get('guest_stop').disabled,false);
});

test('editing invalidates a preview and cannot submit the old proposal',async()=>{
  const h=setup([initial(),proposal()]);await flush();await h.emit('guest_form','submit');
  await h.emit('guest_form','input');await h.emit('guest_confirm','click');
  assert.equal(h.elements.get('guest_confirmation').hidden,true);assert.equal(h.calls.length,2);
});

test('fractional duration never sends a preview',async()=>{
  const h=setup();await flush();h.elements.get('guest_duration').value='1.5';await h.emit('guest_form','submit');
  assert.equal(h.calls.length,1);assert.match(h.elements.get('guest_notice').textContent,/prüfen/);
});

test('vehicle SoC target blocks guest creation without changing settings',async()=>{
  const h=setup([{...initial(),vehicle_target_active:true}]);await flush();
  assert.equal(h.elements.get('guest_fields').disabled,true);assert.match(h.elements.get('guest_notice').textContent,/Batterieziel/);
  assert.equal(h.calls.length,1);
});

test('lost POST response cannot automatically repeat or extend a guest order',async()=>{
  const h=setup([initial(),proposal(),new Error('lost'),initial()]);await flush();await h.emit('guest_form','submit');await h.emit('guest_confirm','click');
  assert.equal(h.calls.filter(c=>c.method==='POST').length,2);assert.equal(h.calls.at(-1).method,undefined);
  assert.match(h.elements.get('guest_notice').textContent,/nicht bestätigt/);
});

test('a hidden page discards a late preview and invalidates confirmation',async()=>{
  let resolve;const h=setup([initial(),()=>new Promise(r=>{resolve=r;})]);await flush();
  const submitted=h.emit('guest_form','submit');await flush();h.document.hidden=true;h.docEvents.visibilitychange();resolve(proposal());await submitted;
  assert.equal(h.elements.get('guest_confirmation').hidden,true);assert.equal(h.elements.get('guest_fields').disabled,true);
  assert.equal(h.timers.size,0);
});

test('other jobs cannot be stopped from the guest-specific button',async()=>{
  const h=setup([{...initial(),job:{...proposal().request,kind:'scheduled',state:'charging',holding:true}}]);await flush();
  assert.equal(h.elements.get('guest_stop').disabled,true);await h.emit('guest_stop','click');assert.equal(h.calls.length,1);
});

test('guest stop and explicit clear remain separate actions',async()=>{
  const active={...proposal().request,state:'charging',delivered_kwh:1,holding:true};
  const stopped={...active,state:'stopped'};
  const h=setup([{...initial(),job:active},{schema:1},{...initial(),revision:'r2',job:stopped},{schema:1},initial()]);await flush();
  await h.emit('guest_stop','click');assert.equal(JSON.parse(h.calls[1].body).action,'stop');
  assert.equal(h.elements.get('guest_clear').disabled,false);await h.emit('guest_clear','click');
  assert.equal(JSON.parse(h.calls[3].body).action,'clear');assert.equal(JSON.parse(h.calls[3].body).confirm,true);
  assert.equal(h.elements.get('guest_fields').disabled,false);
});

test('missing CSRF or bad schema disables actions and hides stale progress',async()=>{
  const h=setup([{...initial(),csrf:undefined}],'en');await flush();
  assert.equal(h.elements.get('guest_fields').disabled,true);assert.match(h.elements.get('guest_notice').textContent,/No current response/);
});

test('recovery clears an offline notice without requiring an edit',async()=>{
  const h=setup([new Error('offline'),initial()]);await flush();
  assert.equal(h.elements.get('guest_fields').disabled,true);
  const timer=[...h.timers.values()].find(t=>t.ms===5000);await timer.fn();await flush();
  assert.equal(h.elements.get('guest_fields').disabled,false);
  assert.match(h.elements.get('guest_notice').textContent,/Bereit/);
});

test('guest text follows the page language set by the server',async()=>{
  for(const [language,ready] of [['fr',/^Prêt/],['nl',/^Gereed/],['it',/^Pronto/],['en',/^Ready/]]){
    const h=setup([initial()],language);await flush();assert.match(h.elements.get('guest_notice').textContent,ready);
  }
});

test('a finished order that handed control back allows a new guest order',async()=>{
  const done={...proposal().request,state:'reached',delivered_kwh:15,holding:false};
  const h=setup([{...initial(),job:done}]);await flush();
  assert.equal(h.elements.get('guest_fields').disabled,false);assert.equal(h.elements.get('guest_stop').disabled,true);
  assert.equal(h.elements.get('guest_clear').disabled,false);assert.match(h.elements.get('guest_detail').textContent,/Home Assistant steuert/);
  const other=setup([{...initial(),job:{...done,kind:'scheduled'}}]);await flush();
  assert.equal(other.elements.get('guest_fields').disabled,false);assert.equal(other.elements.get('guest_state').textContent,'Kein Auftrag');
  const held=setup([{...initial(),job:{...done,holding:true}}]);await flush();
  assert.equal(held.elements.get('guest_fields').disabled,true);assert.equal(held.elements.get('guest_stop').disabled,true);
});
