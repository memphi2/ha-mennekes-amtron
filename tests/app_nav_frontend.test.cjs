"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = path.join(__dirname, "../custom_components/mennekes_amtron_rtu/device");
const source = fs.readFileSync(path.join(root, "app_nav.js"), "utf8");
const pages = ["app_overview.html", "app_settings.html", "app_registers.html", "app_runtime.html", "app_jobs.html", "app_guest.html"];

function setup(language, current, {hash = "", debugTab = false} = {}) {
  const attributes = {}, events = {};
  const nav = {dataset:{nav:current}, children:[], setAttribute(name, value) { attributes[name] = value; }, replaceChildren(...links) { this.children = links; }};
  const debug = {clicks:0, click() { this.clicks++; }};
  const location = {hash};
  const document = {documentElement:{lang:language}, querySelectorAll:selector => selector === "nav[data-nav]" ? [nav] : [],
    getElementById:id => id === "debug_tab" && debugTab ? debug : null,
    createElement:() => ({attributes:{}, setAttribute(name, value) { this.attributes[name] = value; }})};
  vm.runInNewContext(source, {document, location, addEventListener(name, fn) { events[name] = fn; }}, {filename:"app_nav.js"});
  return {nav, attributes, debug, location, events};
}

test("every page loads the shared navigation and names its own entry", () => {
  for (const page of pages) {
    const html = fs.readFileSync(path.join(root, page), "utf8");
    assert.match(html, /<script src="\.\/app_nav\.js" defer><\/script>/, page);
    assert.match(html, /<nav class="overview-nav" aria-label="Service" data-nav="(overview|connection|registers|runtime|jobs|guest)"><\/nav>/, page);
    assert.match(html, /<html lang="en">/, page);
  }
});

test("the links follow the page language and mark the current page", () => {
  for (const [language, overview, guest, label] of [
    ["en", "Overview", "Guest charging", "Service pages"], ["de", "Übersicht", "Gastladen", "Service-Seiten"],
    ["fr", "Vue d’ensemble", "Recharge invité", "Pages du service"], ["nl", "Overzicht", "Gastladen", "Servicepagina’s"],
    ["it", "Panoramica", "Ricarica ospite", "Pagine del servizio"], ["xx", "Overview", "Guest charging", "Service pages"],
  ]) {
    const h = setup(language, "jobs");
    assert.equal(h.nav.children.length, 7);
    assert.equal(h.nav.children[0].textContent, overview); assert.equal(h.nav.children[6].textContent, guest);
    assert.equal(h.attributes["aria-label"], label);
    assert.deepEqual(h.nav.children.map(link => link.href), ["./", "./connection", "./connection#debug", "./registers", "./runtime", "./jobs", "./guest"]);
    assert.deepEqual(h.nav.children.filter(link => link.attributes["aria-current"] === "page").map(link => link.href), ["./jobs"]);
  }
});

test("the debug link opens the debug tab on the connection page only", () => {
  const h = setup("de", "connection", {hash:"#debug", debugTab:true});
  assert.equal(h.debug.clicks, 1);
  h.location.hash = ""; h.events.hashchange(); assert.equal(h.debug.clicks, 1);
  h.location.hash = "#debug"; h.events.hashchange(); assert.equal(h.debug.clicks, 2);
  const other = setup("de", "jobs", {hash:"#debug"});
  assert.equal(other.events.hashchange, undefined);
});
