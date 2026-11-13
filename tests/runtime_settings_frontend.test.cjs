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
async function harness(responses=[payload()], language="de") {
  const nodes=new Map();const el=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
  const calls=[];const timers=new Map();let next=0;
  const context={document:{documentElement:{lang:language},getElementById:el,querySelectorAll:()=>[],createElement:()=>new Element()},AbortController,
    setTimeout:(fn,delay)=>{const id=++next;timers.set(id,{fn:()=>{timers.delete(id);return fn();},delay});return id;},clearTimeout:id=>timers.delete(id),
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
  h.responses.push(payload({changes:[{key:"scan_interval",effective:5,proposed:10,effect:"next_cycle"}]}));
  await h.el("runtime_form").emit("submit");assert.equal(h.calls.length,2);assert.equal(h.calls[1].url,"./api/runtime/preview");assert.equal(h.calls[1].options.headers["X-Amtron-CSRF"],"c");assert.equal(h.el("runtime_save").disabled,false);
  await h.el("runtime_form").emit("input");assert.equal(h.el("runtime_save").disabled,true);await h.el("runtime_save").emit("click");assert.equal(h.calls.length,2);
});
test("fields and confirmation are disabled while a request is in flight",async()=>{
  const h=await harness();let finish;h.responses.push(()=>new Promise(resolve=>{finish=resolve;}));
  const pending=h.el("runtime_form").emit("submit");await h.flush();
  assert.ok(names.every(key=>h.el(key).disabled));assert.equal(h.el("runtime_save").disabled,true);
  await h.el("runtime_form").emit("submit");assert.equal(h.calls.length,2);finish(payload());await pending;
});
test("a lower cap saves without a restart and issues no charging command",async()=>{
  const h=await harness();h.el("current_cap").value="8";
  const result=payload({active:{...fields,current_cap:8},saved:{...fields,current_cap:8}});
  h.responses.push(payload({changes:[{key:"current_cap",effective:16,proposed:8,effect:"immediate"}]}),result);
  await h.el("runtime_form").emit("submit");
  assert.equal(h.el("runtime_changes").children[0].textContent,"Service-Stromobergrenze (A): 16 → 8 · Gilt sofort");
  await h.el("runtime_save").emit("click");
  assert.equal(h.calls.length,3);assert.equal(JSON.parse(h.calls[2].options.body).confirm,true);assert.equal(h.el("runtime_save").disabled,true);
  assert.equal(h.el("runtime_restart").textContent,"Für diese Änderung ist kein Neustart nötig.");
  assert.ok(h.calls.every(call=>!call.url.includes("command")));
});
test("every effect is named in the page language",async()=>{
  const changes=[["scan_interval","next_cycle"],["current_cap","immediate"],["phase_switch_grace_seconds","next_switch"]].map(([key,effect])=>({key,effective:1,proposed:2,effect}));
  for(const [language,words] of [["en",["Next polling cycle","Applies at once","Next phase-switch wait"]],["de",["Nächster Pollzyklus","Gilt sofort","Nächste Wartezeit beim Phasenwechsel"]],["fr",["Prochain cycle d’interrogation","S’applique immédiatement","Prochaine attente de commutation de phase"]],["nl",["Volgende uitleescyclus","Geldt meteen","Volgende wachttijd voor faseomschakeling"]],["it",["Prossimo ciclo di lettura","Vale subito","Prossima attesa di commutazione di fase"]]]){
    const h=await harness([payload(),payload({changes})],language);await h.el("runtime_form").emit("submit");
    assert.deepEqual(h.el("runtime_changes").children.map(node=>node.textContent.split(" · ")[1]),words);
  }
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

test("a connection change saved in the Supervisor form is visible immediately",async()=>{
  const h=await harness([payload({restart_required:true,restart_fields:["connection"]})]);
  assert.equal(h.el("runtime_preview").hidden,false);assert.equal(h.el("runtime_changes").children.length,0);assert.equal(h.el("runtime_restart").textContent,"Neustart erforderlich: Verbindung");
});
test("restart notes use the page language set by the server",async()=>{
  for(const [language,note] of [["en","Requires restart: Connection, Read-only mode"],["de","Neustart erforderlich: Verbindung, Nur-Lesen-Modus"],["fr","Redémarrage requis: Connexion, Mode lecture seule"],["nl","Herstart vereist: Verbinding, Alleen-lezenmodus"],["it","Riavvio necessario: Connessione, Modalità sola lettura"]]){
    const h=await harness([payload({restart_required:true,restart_fields:["connection","read_only"]})],language);
    assert.equal(h.el("runtime_restart").textContent,note);
  }
});
test("saved values that are not in use yet point to saving them here",async()=>{
  for(const [language,start] of [["en","Some saved values"],["de","Einige gespeicherte Werte"],["fr","Certaines valeurs"],["nl","Sommige opgeslagen"],["it","Alcuni valori"]]){
    const h=await harness([payload({saved:{...fields,current_cap:8}})],language);
    assert.ok(h.el("runtime_notice").textContent.startsWith(start));assert.equal(h.el("current_cap").value,"8");
  }
  const h=await harness([payload({saved:{...fields,current_cap:8},write_blocked:true})]);assert.match(h.el("runtime_notice").textContent,/ungewissen/);
});
