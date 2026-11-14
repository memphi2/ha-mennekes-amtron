"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = path.join(__dirname, "../custom_components/mennekes_amtron_rtu/device");
const html = fs.readFileSync(path.join(root, "app_overview.html"), "utf8");
const source = fs.readFileSync(path.join(root, "app_overview.js"), "utf8");
const python = fs.readFileSync(path.join(root, "operation.py"), "utf8");
const flush = () => new Promise(resolve => setImmediate(resolve));
const START = "2026-11-14T02:00:00+00:00", END = "2026-11-14T06:00:00+00:00";
const operation = (changes = {}) => ({controller:"departure_plan", waiting:"until_planned_start",
  next:{event:"plan_start", at:START}, target:{kind:"level", value:80, deadline:END}, vehicle:"confirmed", ...changes});
const overview = summary => ({schema:1, configured:true, available:true, state:"paused", reason:"pause_requested",
  read_only:false, heartbeat:"ok", pending:null, measurements:{power_w:0, session_kwh:1.5, current_limit_a:1},
  targets:{energy_kwh:0, soc_percent:80}, operation:summary, commands:[]});
class Node {
  constructor() { this.textContent = ""; this.hidden = false; this.children = []; }
  set innerHTML(_value) { throw new Error("HTML insertion prohibited"); }
  appendChild(node) { this.children.push(node); }
  replaceChildren(...nodes) { this.children = nodes; }
}
function setup(language, data) {
  const ids = new Map([...html.matchAll(/id="([^"]+)"/g)].map(match => [match[1], new Node()]));
  const texts = [...html.matchAll(/data-overview="([^"]+)"/g)].map(match => Object.assign(new Node(), {dataset:{overview:match[1]}}));
  const document = {hidden:false, documentElement:{lang:language}, getElementById:id => ids.get(id),
    querySelectorAll:() => texts, createElement:() => new Node(), addEventListener() {}};
  const context = {document, AbortController, Date, Number, Array, setTimeout:() => 1, clearTimeout() {}, addEventListener() {},
    fetch:async () => ({ok:true, status:200, json:async () => data})};
  vm.runInNewContext(source, context, {filename:"app_overview.js"});
  return {ids, texts};
}
const put = (h, id) => h.ids.get(id).textContent;
// The Python tuple of a vocabulary, so that the page cannot miss one of its values.
const values = name => [...python.match(new RegExp(`^${name} = \\(([^)]*)\\)`, "m"))[1].matchAll(/"([a-z_]+)"/g)].map(match => match[1]);

test("the control card explains the service's summary in the page language", async () => {
  for (const [language, controller, waiting, step, vehicle, by] of [
    ["en", "Departure planning", "Paused until the planned start", "Planned charge starts", "Confirmed", "by"],
    ["de", "Abfahrtsplanung", "Pausiert bis zum geplanten Start", "Geplantes Laden startet", "Bestätigt", "bis"],
    ["fr", "Planification du départ", "En pause jusqu’au démarrage prévu", "La recharge planifiée démarre", "Confirmé", "d’ici"],
    ["nl", "Vertrekplanning", "Gepauzeerd tot de geplande start", "Gepland laden start", "Bevestigd", "tegen"],
    ["it", "Pianificazione della partenza", "In pausa fino all’avvio pianificato", "Inizia la ricarica pianificata", "Confermato", "entro"],
  ]) {
    const h = setup(language, overview(operation())); await flush();
    assert.equal(h.ids.get("operation").hidden, false);
    assert.equal(put(h, "op_controller"), controller);
    assert.equal(put(h, "op_waiting"), waiting);
    assert.ok(put(h, "op_step").startsWith(`${step} · `), put(h, "op_step"));
    assert.equal(put(h, "op_car"), vehicle);
    assert.match(put(h, "op_goal"), new RegExp(`^80 % ${by} .*2026`));
  }
});

test("targets without a time, energy targets and an idle wallbox read plainly", async () => {
  let h = setup("en", overview(operation({controller:"direct", waiting:"none", next:{event:"none", at:null},
    target:{kind:"none", value:null, deadline:null}, vehicle:"connected"}))); await flush();
  assert.deepEqual(["op_controller", "op_waiting", "op_step", "op_goal", "op_car"].map(id => put(h, id)),
    ["Direct control", "Nothing is holding the charge", "Nothing scheduled", "Off", "Connected"]);
  h = setup("en", overview(operation({target:{kind:"energy", value:12.5, deadline:null}}))); await flush();
  assert.equal(put(h, "op_goal"), "12.5 kWh");
  h = setup("en", overview(operation({next:{event:"plan_start", at:"soon"}}))); await flush();
  assert.equal(put(h, "op_step"), "Nothing scheduled");
});

test("a service without a summary hides the card, a broken one hides the page", async () => {
  let h = setup("en", overview(null)); await flush();
  assert.equal(h.ids.get("operation").hidden, true);
  assert.equal(h.ids.get("overview_data").hidden, false);
  h = setup("en", overview({controller:"direct"})); await flush();
  assert.equal(h.ids.get("overview_data").hidden, true);
  assert.match(put(h, "overview_notice"), /unavailable/);
});

test("every value of the summary has a text in all five languages", () => {
  const words = Object.fromEntries([...source.matchAll(/^ {4}([a-z_]+): (\[.*\]),?$/gm)].map(match => [match[1], JSON.parse(match[2])]));
  const keys = [["CONTROLLERS", "op_"], ["WAITING", "wait_"], ["EVENTS", "step_"], ["VEHICLES", "car_"]]
    .flatMap(([name, prefix]) => { const found = values(name); assert.ok(found.length >= 4, name); return found.map(value => prefix + value); });
  for (const key of [...keys, "op_title", "op_wait", "op_next", "op_target", "op_vehicle", "op_by", "op_hint"]) {
    assert.equal(words[key]?.length, 5, key);
    assert.ok(words[key].every(value => typeof value === "string" && value), key);
  }
});

test("waiting guidance follows the current reason and disappears during ordinary charging", async () => {
  const cases = [
    ["vehicle_unconfirmed", /Fahrzeug/], ["waiting_for_soc", /Ladestand/],
    ["paused", /Ladepause/], ["read_only", /App-Konfiguration/],
    ["wallbox_unavailable", /Verbindung/], ["target_reached", /Ziel/],
  ];
  for (const [waiting, expected] of cases) {
    const h = setup("de", overview(operation({waiting})));
    await flush();
    assert.match(put(h, "op_advice"), expected);
  }
  const h = setup("de", overview(operation({waiting: "none"})));
  await flush();
  assert.equal(put(h, "op_advice"), "");
});
