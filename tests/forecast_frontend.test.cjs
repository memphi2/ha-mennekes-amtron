"use strict";
const {test}=require("node:test");
const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const vm=require("node:vm");
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const source=fs.readFileSync(path.join(__dirname,"../custom_components/mennekes_amtron_rtu/device/app_jobs.js"),"utf8");
const estimate={basis:"ac_meter",state:"estimate",source:"live",power_kw:9,samples:4,earliest_finish:"2026-11-16T07:10:00+00:00",latest_finish:"2026-11-16T07:35:00+00:00",observed_at:"2026-11-16T06:00:00+00:00",deadline_status:"likely",history_storage:"ok"};
function harness(forecast=estimate,language="de"){
  const elements=new Map(),timers=new Map(),events={},windows={},calls=[];let index=0, fail=false;
  const el=id=>{if(!elements.has(id))elements.set(id,{value:"",hidden:false,textContent:"",disabled:false,events:{},addEventListener(k,f){this.events[k]=f;},reportValidity(){return true;},set innerHTML(_v){throw Error("HTML injection");}});return elements.get(id);};
  let state={schema:1,revision:"fixture",csrf:"fixture-csrf",scope:"fixture-scope",ready:true,storage_error:false,available:true,present:true,permitted:true,meter_fresh:true,max_current_a:16,forecast,job:{state:"charging",reason:"charging",start_at:"2026-11-16T06:00:00Z",finish_at:"2026-11-16T08:00:00Z",current_a:12,energy_kwh:20,delivered_kwh:5,scope_ended:false,holding:true}};
  const document={hidden:false,documentElement:{lang:language},getElementById:el,querySelectorAll:()=>[],addEventListener:(k,f)=>{events[k]=f;}};
  const fetch=async(url,options={})=>{calls.push({url,options});assert.equal(options.method,undefined);if(fail)throw Error("offline");return {ok:true,json:async()=>structuredClone(state)};};
  vm.runInNewContext(source,{document,Date,Number,String,JSON,Error,AbortController,fetch,crypto:{randomUUID:()=>"a".repeat(32)},setTimeout:(fn,delay)=>{timers.set(++index,{fn,delay});return index;},clearTimeout:id=>timers.delete(id),addEventListener:(k,f)=>{windows[k]=f;}});
  return {el,calls,document,events,windows,setState:changes=>{state={...state,...changes};},offline:()=>{fail=true;},async refresh(){const timer=[...timers.values()].find(t=>t.delay===5000);assert.ok(timer);await timer.fn();await flush();}};
}

test("the advisory range shares the existing GET and never issues commands",async()=>{
  const h=harness();await flush();assert.equal(h.calls.length,1);assert.equal(h.calls[0].url,"./api/jobs");
  assert.equal(h.el("job_forecast").hidden,false);assert.match(h.el("job_forecast_summary").textContent,/→.*Innerhalb/);
  assert.match(h.el("job_forecast_detail").textContent,/9 kW.*Aktuelle Messintervalle: 4/);
});
test("historical estimates are clearly separate from live measurements",async()=>{
  const h=harness({...estimate,source:"history",deadline_status:"at_risk"});await flush();
  assert.match(h.el("job_forecast_detail").textContent,/Frühere Messintervalle/);assert.match(h.el("job_forecast_summary").textContent,/zu kurz/);
});
test("an estimate disappears when its readings become old",async()=>{
  const h=harness();await flush();h.setState({forecast:{basis:"ac_meter",state:"stale"}});await h.refresh();
  assert.equal(h.el("job_forecast_summary").textContent,"Messbasis ist veraltet");assert.equal(h.el("job_forecast_detail").textContent,"");
});
for(const field of ["earliest_finish","latest_finish","observed_at"]){
  test(`invalid ${field} cannot appear as an end-time estimate`,async()=>{
    const h=harness({...estimate,[field]:"invalid"});await flush();assert.equal(h.el("job_forecast_summary").textContent,"Keine verlässliche aktuelle Prognose");assert.equal(h.el("job_forecast_detail").textContent,"");
  });
}
for(const changes of [{power_kw:Infinity},{samples:2},{earliest_finish:"2026-11-16T08:10:00Z"},{source:"<script>"},{basis:"soc"},{state:"__proto__"}]){
  test(`unsafe or incomplete advisory data is hidden: ${JSON.stringify(changes)}`,async()=>{
    const h=harness({...estimate,...changes});await flush();assert.equal(h.el("job_forecast_summary").textContent,"Keine verlässliche aktuelle Prognose");
  });
}
test("older service responses and no-job responses retain their ordinary UI",async()=>{
  const h=harness(null);await flush();assert.equal(h.el("job_forecast").hidden,true);assert.match(h.el("job_state").textContent,/Laden/);
  h.setState({forecast:estimate,job:null});await h.refresh();assert.equal(h.el("job_forecast").hidden,true);
});
test("optional history errors do not disable valid manual controls",async()=>{
  const h=harness({...estimate,history_storage:"unavailable"});await flush();assert.match(h.el("job_forecast_storage").textContent,/nicht dauerhaft gespeichert/);assert.equal(h.el("job_stop").disabled,false);
});
test("offline and page lifecycle clear old forecast text",async()=>{
  const h=harness();await flush();h.windows.pagehide();assert.equal(h.el("job_forecast").hidden,true);assert.equal(h.el("job_forecast_summary").textContent,"");
  h.windows.pageshow();await flush();assert.equal(h.el("job_forecast").hidden,false);
  h.document.hidden=true;h.events.visibilitychange();assert.equal(h.el("job_forecast").hidden,true);
  h.document.hidden=false;h.events.visibilitychange();await flush();h.offline();await h.refresh();assert.equal(h.el("job_forecast").hidden,true);assert.equal(h.el("job_forecast_detail").textContent,"");
});
test("English unknown/stopped states remain text-only and do not become ranges",async()=>{
  const h=harness({basis:"ac_meter",state:"awaiting_meter"},"en");await flush();assert.match(h.el("job_forecast_summary").textContent,/new meter reading/);
  h.setState({forecast:{basis:"ac_meter",state:"stopped"}});await h.refresh();assert.match(h.el("job_forecast_summary").textContent,/Order ended/);
});
test("the forecast follows the page language, also in French, Dutch and Italian",async()=>{
  for(const [language,expected,detail] of [["fr",/Dans la plage horaire acceptée/,/9 kW.*Intervalles mesurés actuels: 4/],["nl",/Binnen het aanvaarde tijdvenster/,/9 kW.*Actuele meetintervallen: 4/],["it",/Entro la finestra oraria accettata/,/9 kW.*Intervalli misurati attuali: 4/]]){
    const h=harness(estimate,language);await flush();
    assert.match(h.el("job_forecast_summary").textContent,expected);assert.match(h.el("job_forecast_detail").textContent,detail);
  }
});
