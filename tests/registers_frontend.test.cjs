"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = path.join(__dirname, "../custom_components/mennekes_amtron_rtu/device");
const html = fs.readFileSync(path.join(root, "app_registers.html"), "utf8");
const source = fs.readFileSync(path.join(root, "app_registers.js"), "utf8");
const flush = () => new Promise(resolve => setImmediate(resolve));
const registers = () => ({schema:1, registers:[
  {key:"cp_state", address:0x0122, datatype:"uint16", access:"read", raw:[3], value:3, enum:"ev_connected", status:"ok", reason:null, updated_at:"2026-11-13T10:00:00Z", attempted_at:"2026-11-13T10:00:00Z"},
  {key:"serial", address:0x0100, datatype:"string", access:"read", raw:[], redacted:true, status:"degraded", reason:"overdue", updated_at:null, attempted_at:null},
]});
class Node {
  constructor(text = "") { this.textContent = text; this.children = []; this.value = ""; this.checked = false; this.listeners = {}; }
  set innerHTML(_value) { throw new Error("HTML insertion prohibited"); }
  appendChild(node) { this.children.push(node); }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(event, fn) { this.listeners[event] = fn; }
}
function setup(language, responses = [registers()]) {
  const ids = new Map([...html.matchAll(/id="([^"]+)"/g)].map(match => [match[1], new Node()]));
  ids.get("register_status").value = "all"; ids.get("register_order").value = "address";
  const texts = [...html.matchAll(/data-text="([^"]+)"/g)].map(match => Object.assign(new Node(), {dataset:{text:match[1]}}));
  const calls = [];
  const document = {hidden:false, documentElement:{lang:language}, getElementById:id => ids.get(id),
    querySelectorAll:selector => selector === "[data-text]" ? texts : [], createElement:() => new Node(), addEventListener() {}};
  const context = {document, AbortController, Date, Number, String, setTimeout:() => 1, clearTimeout() {}, addEventListener() {},
    fetch:async (url, options) => { calls.push({url, options}); const data = responses.shift(); return {ok:true, json:async () => data}; }};
  vm.runInNewContext(source, context, {filename:"app_registers.js"});
  return {ids, texts, calls};
}
const cell = (h, row, column) => h.ids.get("register_rows").children[row].children[column].textContent;

test("registers follow the page language and translate enumerations", async () => {
  for (const [language, connected, status, degraded] of [
    ["en", "Vehicle connected", "Quality", "Degraded"], ["de", "Fahrzeug verbunden", "Datenqualität", "Eingeschränkt"],
    ["fr", "Véhicule connecté", "Qualité", "Dégradé"], ["nl", "Voertuig aangesloten", "Kwaliteit", "Verminderd"],
    ["it", "Veicolo collegato", "Qualità", "Degradato"],
  ]) {
    const h = setup(language); await flush();
    assert.equal(h.calls.length, 1); assert.equal(h.calls[0].options.method, undefined);
    assert.ok(cell(h, 1, 3).endsWith(`\n${connected}`), cell(h, 1, 3));
    assert.equal(h.texts.find(node => node.dataset.text === "status").textContent, status);
    assert.equal(h.texts.find(node => node.dataset.text === "degraded").textContent, degraded);
    assert.match(h.ids.get("register_summary").textContent, new RegExp(degraded));
  }
});

test("an unknown page language falls back to English and redacts the serial number", async () => {
  const h = setup("xx"); await flush();
  assert.equal(cell(h, 0, 2), "Redacted"); assert.equal(cell(h, 0, 6), "Degraded\nRefresh overdue");
  assert.match(h.ids.get("register_notice").textContent, /^Live/);
});

test("every status option in the filter has a translation", () => {
  const options = [...html.matchAll(/<option value="([^"]+)"(?: data-text="([^"]+)")?>/g)];
  assert.ok(options.length >= 8);
  for (const [, value, key] of options) assert.ok(value === "ok" || key, `${value} has no data-text`);
});
