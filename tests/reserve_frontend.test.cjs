"use strict";
const {test}=require("node:test");
const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const vm=require("node:vm");
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function harness(page,{legacy=false,lost=false,language="de-DE"}={}){
  const elements=new Map(),calls=[],timers=new Map();let timer=0;
  const el=id=>{if(!elements.has(id))elements.set(id,{value:"",checked:false,textContent:"",hidden:false,disabled:false,dataset:{},events:{},addEventListener(k,f){this.events[k]=f;},replaceChildren(...nodes){this.children=nodes;},reportValidity(){return true;}});return elements.get(id);};
  const days=Array.from({length:7},(_,i)=>({...el("day-"+i),value:String(i),checked:i===0}));
  const recipe={id:"a".repeat(32),vehicle_profile:"fixture",timezone:"UTC",weekdays:[0],departure_time:"08:00",charge_minutes:120,current_a:12,energy_kwh:10,enabled:true,ambiguous_time:"earlier",exceptions:[]};
  let state={schema:1,revision:"rev-1",csrf:"test-csrf",scope:"scope",ready:true,storage_error:false,available:true,present:true,permitted:true,meter_fresh:true,max_current_a:16,job:null,plans:legacy?[{plan:recipe,upcoming:[]}]:[],active:null};
  for(const [key,value] of Object.entries({job_energy:"10",job_current:"12",job_reserve:"0",job_boost_minutes:"30",reserve_kwh:"0"}))el(key).value=value;
  const document={hidden:false,documentElement:{lang:""},getElementById:el,querySelectorAll:q=>q==='input[name="weekday"]'?days:[],createElement:()=>({textContent:""}),addEventListener(){}};
  const fetch=async(url,options={})=>{
    const body=options.body?JSON.parse(options.body):null;calls.push({url,options,body});
    if(!body)return {ok:true,json:async()=>structuredClone(state)};
    let data;
    if(body.action==="preview")data={schema:1,revision:state.revision,scope:state.scope,request:body.request};
    else if(body.action==="create"){
      state={...state,job:{...body.request,state:"charging",reason:"charging",delivered_kwh:0,reserve_reached:false},revision:"rev-2"};
      if(lost)throw new Error("lost response");data=state;
    }else if(body.action==="preview_plan")data={schema:1,revision:state.revision,plan:body.plan,upcoming:[],will_stop_active:false};
    else if(body.action==="save_plan"){state={...state,revision:"rev-2",plans:[{plan:body.plan,upcoming:[]}]};data=state;}
    else if(body.action==="preview_activation"){
      const p=state.plans[0].plan;
      data={schema:1,revision:state.revision,vehicle_profile:p.vehicle_profile,active_until:"2026-11-23T08:00:00Z",request:{id:"c".repeat(32),current_a:p.current_a,energy_kwh:p.energy_kwh,finish_at:"2026-11-16T08:00:00Z",...(p.reserve_kwh?{reserve_kwh:p.reserve_kwh}:{})},confirmation:"frozen"};
    }else if(body.action==="activate"){state={...state,active:{plan_id:recipe.id,vehicle_profile:"fixture",until:body.proposal.active_until},revision:"rev-3"};data=state;}
    else throw new Error("unexpected action");
    return {ok:true,json:async()=>structuredClone(data)};
  };
  const source=fs.readFileSync(path.join(__dirname,`../custom_components/mennekes_amtron_rtu/device/app_${page}.js`),"utf8");
  vm.runInNewContext(source,{document,navigator:{language},fetch,crypto:{randomUUID:()=>"bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"},Date,Number,String,JSON,Error,AbortController,Option:function(text,value){this.text=text;this.value=value;},confirm:()=>true,setTimeout:fn=>{timers.set(++timer,fn);return timer;},clearTimeout:id=>timers.delete(id),addEventListener(){}});
  return {el,calls,state:()=>state,async fire(id,event="click"){await el(id).events[event]({preventDefault(){}});await flush();},posts:()=>calls.filter(c=>c.body)};
}

test("job reserve is visible in preview and exact confirmation; editing discards it",async()=>{
  const h=harness("jobs");await flush();h.el("job_reserve").value="5";await h.fire("job_form","submit");
  assert.equal(h.posts()[0].body.request.reserve_kwh,5);assert.match(h.el("job_summary").textContent,/Sofortreserve.*5/);
  await h.fire("job_form","input");await h.fire("job_confirm");assert.equal(h.posts().length,1);
  await h.fire("job_form","submit");await h.fire("job_confirm");
  assert.equal(h.posts()[2].body.request.reserve_kwh,5);assert.match(h.el("job_progress").textContent,/Reserve noch nicht erreicht/);
});
test("zero reserve preserves old job payloads",async()=>{
  const h=harness("jobs");await flush();await h.fire("job_form","submit");assert.equal(h.posts()[0].body.request.reserve_kwh,undefined);
});
test("lost reserve create response is never retried",async()=>{
  const h=harness("jobs",{lost:true,language:"en-US"});await flush();h.el("job_reserve").value="3";
  await h.fire("job_form","submit");await h.fire("job_confirm");
  assert.deepEqual(h.posts().map(p=>p.body.action),["preview","create"]);assert.equal(h.state().job.reserve_kwh,3);
});
for(const page of ["jobs","weekly"])for(const value of ["-1","11","0.11","NaN"]){
  test(`${page}: invalid reserve ${value} never sends a POST`,async()=>{
    const h=harness(page);await flush();h.el(page==="jobs"?"job_reserve":"reserve_kwh").value=value;
    h.el("vehicle_profile").value="fixture";await h.fire(page==="jobs"?"job_form":"weekly_form","submit");assert.equal(h.posts().length,0);
  });
}
test("weekly reserve persists from recipe preview to signed activation proposal",async()=>{
  const h=harness("weekly");await flush();h.el("vehicle_profile").value="fixture";h.el("reserve_kwh").value="4";
  await h.fire("weekly_form","submit");assert.equal(h.posts()[0].body.plan.reserve_kwh,4);assert.match(h.el("plan_summary").textContent,/Sofortreserve.*4/);
  await h.fire("save_plan");assert.equal(h.posts()[1].body.plan.reserve_kwh,4);assert.equal(Number(h.el("reserve_kwh").value),4);
  h.el("confirm_vehicle").checked=true;await h.fire("confirm_vehicle","change");await h.fire("preview_activation");
  assert.match(h.el("activation_summary").textContent,/Sofortreserve.*4/);await h.fire("activate");
  assert.equal(h.posts().at(-1).body.proposal.request.reserve_kwh,4);assert.equal(h.posts().at(-1).body.proposal.confirmation,"frozen");
});
test("loading an old saved recipe fills zero and keeps it absent on the wire",async()=>{
  const h=harness("weekly",{legacy:true});await flush();h.el("plan_select").value="a".repeat(32);await h.fire("plan_select","change");
  assert.equal(Number(h.el("reserve_kwh").value),0);await h.fire("weekly_form","submit");assert.equal(h.posts()[0].body.plan.reserve_kwh,undefined);
});
