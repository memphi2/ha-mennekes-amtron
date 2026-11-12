"use strict";
const assert = require("node:assert/strict");
const {readFileSync} = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");
const source = readFileSync(path.join(__dirname,"../custom_components/mennekes_amtron_rtu/device/app_runtime.js"),"utf8");
const names=["scan_interval","measurement_interval","current_cap","state_change_interval_seconds","phase_switch_grace_seconds"];
const fields={scan_interval:5,measurement_interval:30,current_cap:16,state_change_interval_seconds:300,phase_switch_grace_seconds:5};
function payload(extra={}) {return {schema:1,active:{...fields},saved:{...fields},proposed:{...fields},changes:[],restart_fields:[],restart_required:false,revision:"r",csrf:"c",write_blocked:false,...extra};}
class Element {
  constructor(){this.hidden=false;this.disabled=false;this.value="";this.textContent="";this.children=[];this.listeners={};}
  set innerHTML(_value){throw new Error("HTML insertion prohibited");}
  appendChild(node){this.children.push(node);}
  replaceChildren(...nodes){this.children=nodes;}
  addEventListener(event,fn){this.listeners[event]=fn;}
  reportValidity(){return true;}
  async emit(event){await this.listeners[event]?.({preventDefault(){}});}
}
async function harness(responses=[payload()]) {
  const nodes=new Map();const el=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
  const calls=[];const timers=new Map();let next=0;
  const context={navigator:{language:"de-DE"},document:{documentElement:{lang:"en"},getElementById:el,querySelectorAll:()=>[],createElement:()=>new Element()},AbortController,
    setTimeout:(fn,delay)=>{timers.set(++next,{fn,delay});return next;},clearTimeout:id=>timers.delete(id),
    fetch:async(url,options)=>{calls.push({url,options});const item=responses.shift();if(item instanceof Error)throw item;const data=typeof item==="function"?await item():item;return {ok:true,json:async()=>data};}};
  vm.runInNewContext(source,context);
  const flush=()=>new Promise(resolve=>setImmediate(resolve));await flush();
  return {el,calls,responses,flush};
}

test("initial load is read-only and displays saved and effective values",async()=>{
  const h=await harness();assert.equal(h.calls.length,1);assert.equal(h.calls[0].url,"./api/runtime");assert.equal(h.calls[0].options.method,undefined);
  assert.equal(h.el("runtime_values").children.length,5);assert.equal(h.el("current_cap").value,"16");assert.equal(h.el("runtime_save").disabled,true);
});
test("preview requires explicit confirmation and input changes invalidate it",async()=>{
  const h=await harness();h.el("scan_interval").value="10";
  h.responses.push(payload({changes:[{key:"scan_interval",effective:5,proposed:10,effect:"live"}]}));
  await h.el("runtime_form").emit("submit");assert.equal(h.calls.length,2);assert.equal(h.calls[1].url,"./api/runtime/preview");assert.equal(h.calls[1].options.headers["X-Amtron-CSRF"],"c");assert.equal(h.el("runtime_save").disabled,false);
  await h.el("runtime_form").emit("input");assert.equal(h.el("runtime_save").disabled,true);await h.el("runtime_save").emit("click");assert.equal(h.calls.length,2);
});
test("fields and confirmation are disabled while a request is in flight",async()=>{
  const h=await harness();let finish;h.responses.push(()=>new Promise(resolve=>{finish=resolve;}));
  const pending=h.el("runtime_form").emit("submit");await h.flush();
  assert.ok(names.every(key=>h.el(key).disabled));assert.equal(h.el("runtime_save").disabled,true);
  await h.el("runtime_form").emit("submit");assert.equal(h.calls.length,2);finish(payload());await pending;
});
test("save reports pending restart and does not issue charging commands",async()=>{
  const h=await harness();h.el("current_cap").value="8";
  const result=payload({saved:{...fields,current_cap:8},restart_fields:["current_cap"],restart_required:true});
  h.responses.push(payload({changes:[{key:"current_cap",effective:16,proposed:8,effect:"restart"}],restart_fields:["current_cap"],restart_required:true}),result);
  await h.el("runtime_form").emit("submit");await h.el("runtime_save").emit("click");
  assert.equal(h.calls.length,3);assert.equal(JSON.parse(h.calls[2].options.body).confirm,true);assert.equal(h.el("runtime_save").disabled,true);assert.match(h.el("runtime_restart").textContent,/Neustart/);
  assert.ok(h.calls.every(call=>!call.url.includes("command")));
});
test("lost save response is not retried and requires reload",async()=>{
  const h=await harness();h.responses.push(payload(),new Error("lost response"));
  await h.el("runtime_form").emit("submit");await h.el("runtime_save").emit("click");await h.el("runtime_save").emit("click");
  assert.equal(h.calls.length,3);assert.equal(h.el("runtime_check").disabled,true);assert.equal(h.el("runtime_reload").disabled,false);assert.equal(h.el("runtime_error").hidden,false);
  h.responses.push(payload());await h.el("runtime_reload").emit("click");await h.flush();assert.equal(h.el("runtime_check").disabled,false);
});
test("failed initial load leaves a usable reload action without editable stale values",async()=>{
  const h=await harness([new Error("offline")]);assert.equal(h.el("runtime_form").hidden,false);assert.equal(h.el("runtime_reload").disabled,false);assert.equal(h.el("runtime_check").disabled,true);assert.equal(h.el("runtime_values").children.length,0);
});
test("uncertain prior save disables all editing",async()=>{
  const h=await harness([payload({write_blocked:true})]);assert.ok(names.every(key=>h.el(key).disabled));assert.equal(h.el("runtime_check").disabled,true);assert.match(h.el("runtime_notice").textContent,/ungewissen/);
});
