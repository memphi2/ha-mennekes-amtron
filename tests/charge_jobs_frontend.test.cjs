"use strict";
const {test}=require("node:test");
const assert=require("node:assert/strict");
const fs=require("node:fs");
const vm=require("node:vm");
const path=require("node:path");
const source=fs.readFileSync(path.join(__dirname,"../custom_components/mennekes_amtron_rtu/device/app_jobs.js"),"utf8");
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function harness({failRead=false, lostCreate=false, lostBoost=false, holdBoostPreview=false, language="de-DE"}={}){
  const elements=new Map(),docEvents={},winEvents={},timers=new Map(),calls=[];let next=0, finishBoostPreview=null;
  const el=id=>{
    if(!elements.has(id))elements.set(id,{value:"",textContent:"",disabled:false,hidden:false,max:"",events:{},addEventListener(name,fn){this.events[name]=fn;},reportValidity(){return true;}});
    return elements.get(id);
  };
  el("job_energy").value="10";el("job_current").value="12";el("job_boost_minutes").value="30";
  let state={schema:1,revision:"revision-1",csrf:"test-csrf",scope:"scope-1",ready:true,storage_error:false,available:true,present:true,permitted:true,meter_fresh:true,max_current_a:16,job:null};
  const document={hidden:false,documentElement:{lang:""},getElementById:el,querySelectorAll:()=>[],addEventListener:(k,f)=>{docEvents[k]=f;}};
  const fetch=async(url,options={})=>{
    calls.push({url,options});
    if(!options.method){if(failRead)throw new Error("offline");return {ok:true,json:async()=>structuredClone(state)};}
    const body=JSON.parse(options.body);
    if(body.action==="preview")return {ok:true,json:async()=>({schema:1,revision:state.revision,scope:state.scope,request:body.request})};
    if(body.action==="preview_boost"){
      const response={ok:true,json:async()=>({schema:1,revision:state.revision,scope:state.scope,until:new Date(Date.now()+body.minutes*60000).toISOString(),current_a:12,energy_kwh:10})};
      if(holdBoostPreview)return new Promise(resolve=>{finishBoostPreview=()=>resolve(response);});
      return response;
    }
    if(body.action==="create"){
      state={...state,revision:"revision-2",job:{...body.request,state:"charging",reason:"charging",delivered_kwh:0,scope_ended:false}};
      if(lostCreate)throw new Error("reply lost");
    }else if(body.action==="boost"){
      state={...state,revision:"boost-2",job:{...state.job,state:"charging",reason:"charging",boost_until:body.until}};
      if(lostBoost)throw new Error("reply lost");
    }else if(body.action==="cancel_boost")state={...state,revision:"boost-3",job:{...state.job,boost_until:null,state:"scheduled",reason:"scheduled"}};
    else if(body.action==="stop")state={...state,revision:"revision-3",job:{...state.job,state:"stopped",reason:"user_stop"}};
    else if(body.action==="clear")state={...state,revision:"revision-4",job:null};
    return {ok:true,json:async()=>structuredClone(state)};
  };
  vm.runInNewContext(source,{document,navigator:{language},crypto:{randomUUID:()=>"12345678-1234-1234-1234-123456789012"},Date,Number,String,JSON,Error,AbortController,fetch,setTimeout:(fn)=>{const id=++next;timers.set(id,()=>{timers.delete(id);return fn();});return id;},clearTimeout:id=>timers.delete(id),addEventListener:(k,f)=>{winEvents[k]=f;}});
  return {el,calls,document,docEvents,winEvents,timers,setState:nextState=>{state={...state,...nextState};},submit:()=>el("job_form").events.submit({preventDefault(){}}),click:id=>el(id).events.click(),finishBoostPreview:()=>finishBoostPreview()};
}

test("initial rendering is passive and never sends a command",async()=>{
  const h=harness();await flush();assert.equal(h.document.documentElement.lang,"de");assert.equal(h.el("job_fields").disabled,false);assert.equal(h.calls.length,1);assert.equal(h.calls[0].options.method,undefined);
});
test("a preview and a separate confirmation precede creation; stop and clear do not resume",async()=>{
  const h=harness();await flush();await h.submit();assert.equal(h.el("job_confirmation").hidden,false);assert.equal(h.calls.filter(c=>c.options.method==="POST").length,1);
  await h.click("job_confirm");await flush();assert.equal(h.el("job_fields").disabled,true);assert.match(h.el("job_state").textContent,/Laden angefordert/);
  await h.click("job_stop");await flush();assert.match(h.el("job_state").textContent,/gestoppt/);
  await h.click("job_clear");await flush();assert.equal(h.el("job_state").textContent,"Kein Auftrag");
  const posts=h.calls.filter(c=>c.options.method==="POST");assert.deepEqual(posts.map(c=>JSON.parse(c.options.body).action),["preview","create","stop","clear"]);assert.ok(posts.every(c=>c.options.headers["X-Amtron-CSRF"]==="test-csrf"));
});
test("editing invalidates a previously confirmed preview",async()=>{
  const h=harness();await flush();await h.submit();h.el("job_form").events.input();await h.click("job_confirm");assert.equal(h.el("job_confirmation").hidden,true);assert.equal(h.calls.filter(c=>c.options.method==="POST").length,1);
});
test("lost create response is resolved by reading state, never by repeating a POST",async()=>{
  const h=harness({lostCreate:true});await flush();await h.submit();await h.click("job_confirm");await flush();assert.equal(h.calls.filter(c=>c.options.method==="POST").length,2);assert.match(h.el("job_state").textContent,/Laden angefordert/);
});
test("failed reads disable all mutations and hide stale status",async()=>{
  const h=harness({failRead:true});await flush();assert.equal(h.el("job_fields").disabled,true);assert.equal(h.el("job_stop").disabled,true);assert.equal(h.el("job_status").hidden,true);assert.match(h.el("job_notice").textContent,/Keine aktuelle/);await h.submit();assert.equal(h.calls.length,1);
});
test("invalid time windows and browser validation never reach the server",async()=>{
  const h=harness();await flush();h.el("job_finish").value="invalid";await h.submit();assert.equal(h.calls.length,1);assert.match(h.el("job_notice").textContent,/Ungültiger/);h.el("job_form").reportValidity=()=>false;await h.submit();assert.equal(h.calls.length,1);
});
test("background tabs invalidate previews and do not poll",async()=>{
  const h=harness();await flush();await h.submit();const before=h.calls.length;h.document.hidden=true;h.docEvents.visibilitychange();await flush();assert.equal(h.calls.length,before);assert.equal(h.el("job_confirmation").hidden,true);h.document.hidden=false;h.docEvents.visibilitychange();await flush();assert.equal(h.calls.length,before+1);h.winEvents.pagehide();assert.equal(h.timers.size,0);const shown=h.calls.length;h.winEvents.pageshow();await flush();assert.equal(h.calls.length,shown+1);assert.equal(h.timers.size,1);
});
test("English labels and changed server revision invalidate old confirmation",async()=>{
  const h=harness({language:"en-US"});await flush();await h.submit();h.setState({revision:"new-revision"});for(const callback of [...h.timers.values()])callback();await flush();assert.equal(h.el("job_confirmation").hidden,true);assert.equal(h.document.documentElement.lang,"en");
});

async function futureOrder(h){
  await flush();
  h.setState({boost_available:true,job:{id:"job-1",state:"scheduled",reason:"scheduled",start_at:new Date(Date.now()+3*3600000).toISOString(),finish_at:new Date(Date.now()+4*3600000).toISOString(),current_a:12,energy_kwh:10,delivered_kwh:0,scope_ended:false,boost_until:null}});
  for(const callback of [...h.timers.values()])callback();
  await flush();
}
test("boost preview is passive; absolute confirmation and cancellation preserve the plan",async()=>{
  const h=harness();await futureOrder(h);assert.equal(h.el("job_boost_preview").disabled,false);
  await h.click("job_boost_preview");assert.match(h.el("job_summary").textContent,/Boost bis/);
  assert.equal(h.el("job_confirmation").hidden,false);
  await h.click("job_confirm");await flush();assert.match(h.el("job_boost_until").textContent,/Boost bis/);
  assert.equal(h.el("job_cancel_boost").disabled,false);
  await h.click("job_cancel_boost");await flush();assert.equal(h.el("job_boost_until").textContent,"");
  assert.match(h.el("job_state").textContent,/Wartet/);
  const posts=h.calls.filter(c=>c.options.method==="POST").map(c=>JSON.parse(c.options.body));
  assert.deepEqual(posts.map(p=>p.action),["preview_boost","boost","cancel_boost"]);
  assert.equal(typeof posts[1].until,"string");assert.equal(posts[1].minutes,undefined);
});
test("boost editing invalidates the preview and fractional durations never send commands",async()=>{
  const h=harness();await futureOrder(h);await h.click("job_boost_preview");
  h.el("job_boost_minutes").events.input();await h.click("job_confirm");
  h.el("job_boost_minutes").value="1.5";await h.click("job_boost_preview");
  assert.match(h.el("job_notice").textContent,/ganze Zahl/);
  assert.equal(h.calls.filter(c=>c.options.method==="POST").length,1);
});
test("lost boost reply is resolved through GET without extending it",async()=>{
  const h=harness({lostBoost:true});await futureOrder(h);await h.click("job_boost_preview");
  await h.click("job_confirm");await flush();assert.match(h.el("job_boost_until").textContent,/Boost bis/);
  assert.equal(h.calls.filter(c=>c.options.method==="POST").length,2);
});
test("a preview response arriving after the tab is hidden cannot restore confirmation",async()=>{
  const h=harness({holdBoostPreview:true});await futureOrder(h);
  const waiting=h.click("job_boost_preview");await flush();
  assert.equal(h.el("job_boost_minutes").disabled,true);
  h.document.hidden=true;h.docEvents.visibilitychange();h.finishBoostPreview();await waiting;
  assert.equal(h.el("job_confirmation").hidden,true);assert.equal(h.el("job_status").hidden,true);
  assert.equal(h.calls.filter(c=>c.options.method==="POST").length,1);
});
