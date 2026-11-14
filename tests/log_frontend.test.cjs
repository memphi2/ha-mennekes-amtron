"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = path.join(__dirname, "../custom_components/mennekes_amtron_rtu/device");
const html = fs.readFileSync(path.join(root, "app_log.html"), "utf8");
const source = fs.readFileSync(path.join(root, "app_log.js"), "utf8");
const flush = () => new Promise(resolve => setImmediate(resolve));
const record = (sequence, extra = {}) => ({
  sequence, time: "2026-11-07T10:11:12.345+00:00", operation: "read", function: "0x03",
  address: "0x0100", count: 1, device_id: 50, duration_ms: 12.4, error: null, exception_code: null,
  values: [{address: "0x0100", register: "evse_state", value: 2, unit: null, enum: "<img src=x>"}], ...extra,
});
const state = (records = [], extra = {}) => ({
  available: true, enabled: true, expires_in: 600, generation: 1, csrf: "token",
  latest_sequence: records.at(-1)?.sequence || 0, records, ...extra,
});
class Node {
  constructor() { this.textContent = ""; this.className = ""; this.children = []; this.value = ""; this.disabled = false; this.listeners = {}; this.scrollTop = 0; this.scrollHeight = 0; this.clientHeight = 0; }
  set innerHTML(_value) { throw new Error("HTML insertion prohibited"); }
  get childElementCount() { return this.children.length; }
  get firstElementChild() { const owner = this; return {remove() { owner.children.shift(); }}; }
  appendChild(node) { this.children.push(node); }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(event, fn) { this.listeners[event] = fn; }
  emit(event) { return this.listeners[event]?.(); }
}
function setup(responses, language = "de") {
  const ids = new Map([...html.matchAll(/id="([^"]+)"/g)].map(match => [match[1], new Node()]));
  const texts = [...html.matchAll(/data-text="([^"]+)"/g)].map(match => Object.assign(new Node(), {dataset: {text: match[1]}}));
  const calls = [], timers = [], events = {};
  const document = {hidden: false, documentElement: {lang: language}, getElementById: id => ids.get(id),
    querySelectorAll: selector => selector === "[data-text]" ? texts : [], createElement: () => new Node(), addEventListener(name, fn) { events[name] = fn; }};
  const context = {document, Date, Number, String, Math, JSON, Error, Boolean,
    setTimeout: (fn, delay) => { timers.push({fn, delay}); return timers.length; }, clearTimeout() {},
    addEventListener(name, fn) { events[name] = fn; },
    fetch: async (url, options = {}) => {
      calls.push({url, method: options.method || "GET", body: options.body ? JSON.parse(options.body) : null, headers: options.headers});
      const item = responses.shift();
      if (item instanceof Error) throw item;
      return {ok: true, json: async () => item};
    }};
  vm.runInNewContext(source, context, {filename: "app_log.js"});
  const tick = async () => { await timers.pop().fn(); await flush(); };
  return {ids, calls, timers, events, document, tick, el: id => ids.get(id), lines: () => ids.get("log_lines").children.map(row => row.children.map(part => part.textContent).join(""))};
}

test("the log starts its capture by itself and follows new lines", async () => {
  const h = setup([state([], {enabled: false, expires_in: 0, generation: 0}), state([], {csrf: undefined})]);
  await flush();
  assert.deepEqual(h.calls.map(call => [call.url, call.method, call.body]), [
    ["./api/modbus-debug?after=0", "GET", null], ["./api/modbus-debug", "POST", {enabled: true}],
  ]);
  assert.equal(h.calls[1].headers["X-Amtron-CSRF"], "token");
  assert.match(h.el("log_status").textContent, /^Live · 0 Zeilen/);
  assert.equal(h.timers.at(-1).delay, 1000);
  // The next poll appends what is new.
  const again = setup([state([]), state([record(1), record(2, {operation: "write", error: "timeout"})])]);
  await flush(); await again.tick();
  assert.equal(again.calls[1].url, "./api/modbus-debug?after=0");
  assert.equal(again.lines().length, 2);
  assert.match(again.lines()[0], /LESEN\s+0x0100 ×1\s+OK\s+12\.4 ms\s+evse state=2 \(<img src=x>\)/);
  assert.match(again.lines()[1], /SCHREIBEN.*Keine Antwort/);
  assert.equal(again.el("log_lines").children[1].children[2].className, "failed");
  assert.equal(again.el("log_lines").scrollTop, again.el("log_lines").scrollHeight);
});

test("an expiring capture is renewed while the page is open", async () => {
  const h = setup([state([record(1)], {expires_in: 100}), state([record(1)], {csrf: undefined})]);
  await flush();
  assert.deepEqual(h.calls.map(call => call.method), ["GET", "POST"]);
  assert.equal(h.lines().length, 1);
});

test("pause collects, resume shows; filter, clear and a restarted capture", async () => {
  const h = setup([state([record(1)]), state([record(1), record(2, {address: "0x0300"})]), state([record(1)], {generation: 2})]);
  await flush();
  h.el("log_pause").emit("click");
  assert.equal(h.el("log_pause").textContent, "Fortsetzen");
  await h.tick();
  assert.equal(h.lines().length, 1);
  assert.match(h.el("log_status").textContent, /^Angehalten.*2 Zeilen/);
  h.el("log_pause").emit("click");
  assert.equal(h.lines().length, 2);
  h.el("log_filter").value = "0x0300"; h.el("log_filter").emit("input");
  assert.equal(h.lines().length, 1);
  assert.match(h.el("log_status").textContent, /2 Zeilen · 1 angezeigt/);
  h.el("log_filter").value = ""; h.el("log_filter").emit("input");
  // A new generation numbers from one again; its lines are added, not lost.
  await h.tick();
  assert.equal(h.lines().length, 3);
  h.el("log_clear").emit("click");
  assert.equal(h.lines().length, 0);
  assert.equal(h.el("log_download").disabled, true);
});

test("no connection and a silent app are named; a hidden page does not poll", async () => {
  const setupMode = setup([state([], {available: false})], "en");
  await flush();
  assert.match(setupMode.el("log_status").textContent, /^Configure the wallbox connection/);
  const offline = setup([new Error("offline")], "en");
  await flush();
  assert.match(offline.el("log_status").textContent, /^No answer/);
  assert.equal(offline.timers.at(-1).delay, 5000);
  offline.document.hidden = true; offline.timers.length = 0;
  await offline.events.visibilitychange(); await flush();
  assert.equal(offline.calls.length, 1);
});
