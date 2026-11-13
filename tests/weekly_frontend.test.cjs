"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const root = path.join(__dirname, "..", "custom_components", "mennekes_amtron_rtu", "device");
const code = fs.readFileSync(path.join(root, "app_weekly.js"), "utf8");
const html = fs.readFileSync(path.join(root, "app_weekly.html"), "utf8");
const plan = {id:"a".repeat(32),vehicle_profile:"fixture",timezone:"UTC",weekdays:[0,3],departure_time:"07:40",charge_minutes:240,current_a:12,energy_kwh:10,ambiguous_time:"earlier",enabled:true,exceptions:[]};
const state = () => ({schema:1,revision:"r1",ready:true,storage_error:false,plans:[{plan,upcoming:[]}],scope:"b".repeat(64),active:null,job:null,available:true,present:true,max_current_a:16,csrf:"f".repeat(64)});
const proposal = () => ({schema:1,revision:"r1",plan_id:plan.id,vehicle_profile:plan.vehicle_profile,scope:"b".repeat(64),request:{id:"c".repeat(32),finish_at:"2026-11-17T07:40:00Z",current_a:12,energy_kwh:10},active_until:"2026-11-23T05:00:00Z",confirmation:"fixed-confirmation"});
const flush = async () => { for(let i=0;i<6;i++) await new Promise(resolve=>setImmediate(resolve)); };
class Element {
  constructor(value="") {this.value=value;this.checked=false;this.disabled=false;this.hidden=false;this.textContent="";this.dataset={};this.events={};this.children=[];}
  addEventListener(name, fn) {this.events[name]=fn;}
  replaceChildren(...items) {this.children=items;}
  reportValidity() {return true;}
}
async function harness(initial=state()) {
  const nodes=Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(m=>[m[1],new Element()]));
  const days=Array.from({length:7},(_,i)=>new Element(String(i)));
  const timers=new Map(), pageEvents={}, documentEvents={}, requests=[], responses=[initial]; let next=0;
  const document={hidden:false,documentElement:{lang:"de"},getElementById:id=>nodes[id],createElement:()=>new Element(),
    querySelectorAll:selector=>selector==='input[name="weekday"]'?days:[],addEventListener:(name,fn)=>{documentEvents[name]=fn;}};
  const response=data=>({ok:true,json:async()=>data});
  const context={document,Option:class extends Element{constructor(text,value){super(value);this.textContent=text;}},
    crypto:{randomUUID:()=>"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"},AbortController,Date,JSON,Number,String,Boolean,Error,Promise,console,
    setTimeout:(fn,ms)=>{timers.set(++next,{fn,ms});return next;},clearTimeout:id=>timers.delete(id),
    addEventListener:(name,fn)=>{pageEvents[name]=fn;},confirm:()=>true,
    fetch:async(url,options)=>{requests.push({url,...options});const item=responses.shift();if(item instanceof Error)throw item;if(typeof item==="function")return item();if(item===undefined)throw new Error("unexpected fetch");return response(item);}};
  vm.runInNewContext(code,context,{filename:"app_weekly.js"});await flush();
  const trigger=async(id,event="click")=>{nodes[id].events[event]({preventDefault(){}});await flush();};
  const select=async()=>{nodes.plan_select.value=plan.id;await trigger("plan_select","change");nodes.confirm_vehicle.checked=true;await trigger("confirm_vehicle","change");};
  return {nodes,days,timers,document,documentEvents,pageEvents,requests,responses,trigger,select,response};
}

test("activation requires vehicle confirmation, freezes preview and sends the existing CSRF header",async()=>{
  const h=await harness();assert.equal(h.nodes.preview_activation.disabled,true);await h.select();assert.equal(h.nodes.preview_activation.disabled,false);
  h.responses.push(proposal());await h.trigger("preview_activation");assert.equal(h.nodes.activate.disabled,false);
  assert.equal(JSON.parse(h.requests.at(-1).body).action,"preview_activation");
  h.responses.push({...state(),revision:"r2",active:{plan_id:plan.id,vehicle_profile:"fixture",until:proposal().active_until}});
  await h.trigger("activate");const request=h.requests.at(-1);
  assert.equal(request.headers["X-Amtron-CSRF"],"f".repeat(64));assert.equal(JSON.parse(request.body).proposal.confirmation,"fixed-confirmation");
  assert.equal(h.nodes.weekly_active.hidden,false);assert.equal(h.nodes.activation_preview.hidden,true);
});

test("periodic reads do not silently uncheck the vehicle confirmation",async()=>{
  const h=await harness();await h.select();h.responses.push(state());
  const timer=[...h.timers.values()].find(t=>t.ms===5000);timer.fn();await flush();
  assert.equal(h.nodes.confirm_vehicle.checked,true);assert.equal(h.nodes.preview_activation.disabled,false);
});

test("recipe editing invalidates the previous confirmation and saving is separate",async()=>{
  const h=await harness();await h.select();h.responses.push({schema:1,revision:"r1",plan,upcoming:[],will_stop_active:true});
  await h.trigger("weekly_form","submit");assert.equal(h.nodes.weekly_preview.hidden,false);
  assert.equal(h.requests.filter(r=>r.method==="POST").length,1);assert.match(h.nodes.plan_warning.textContent,/stoppt/);
  await h.trigger("weekly_form","input");assert.equal(h.nodes.weekly_preview.hidden,true);assert.equal(h.nodes.save_plan.disabled,true);
  h.responses.push({schema:1,revision:"r1",plan,upcoming:[],will_stop_active:false});await h.trigger("weekly_form","submit");
  h.responses.push({...state(),revision:"r2"});await h.trigger("save_plan");assert.equal(JSON.parse(h.requests.at(-1).body).action,"save_plan");
});

test("invalid exceptions and missing weekdays never generate a POST",async()=>{
  const h=await harness();await h.select();h.nodes.exceptions.value="garbage";await h.trigger("weekly_form","submit");
  assert.equal(h.requests.length,1);h.nodes.exceptions.value="";h.days.forEach(day=>{day.checked=false;});await h.trigger("weekly_form","submit");
  assert.equal(h.requests.length,1);assert.match(h.nodes.weekly_notice.textContent,/Eingaben/);
});

test("lost POST replies are not retried and disable further writes until reload",async()=>{
  const h=await harness();await h.select();h.responses.push(new Error("offline"));await h.trigger("preview_activation");
  assert.equal(h.requests.filter(r=>r.method==="POST").length,1);assert.equal(h.nodes.preview_activation.disabled,true);assert.match(h.nodes.weekly_notice.textContent,/unbestätigt/);
});

test("obsolete POST responses after hiding the tab cannot restore confirmation",async()=>{
  const h=await harness();await h.select();let release;h.responses.push(()=>new Promise(resolve=>{release=resolve;}));
  h.nodes.preview_activation.events.click();await flush();h.document.hidden=true;h.documentEvents.visibilitychange();
  release(h.response(proposal()));await flush();assert.equal(h.nodes.activation_preview.hidden,true);assert.equal(h.nodes.activate.disabled,true);
});

test("offline reads hide current status and have no control side effects",async()=>{
  const h=await harness(new Error("offline"));assert.equal(h.nodes.weekly_fields.disabled,true);assert.equal(h.nodes.weekly_active.hidden,true);
  assert.equal(h.requests.length,1);assert.match(h.nodes.weekly_notice.textContent,/Service-Antwort/);
});

test("remove and deactivate are explicit bounded commands",async()=>{
  const h=await harness();await h.select();h.responses.push({...state(),revision:"r2",plans:[]});await h.trigger("remove_plan");
  assert.equal(JSON.parse(h.requests.at(-1).body).action,"remove_plan");
  const second=await harness({...state(),active:{plan_id:plan.id,vehicle_profile:"fixture",until:proposal().active_until}});
  second.responses.push({...state(),revision:"r2"});await second.trigger("deactivate");assert.equal(JSON.parse(second.requests.at(-1).body).confirm,true);
});
