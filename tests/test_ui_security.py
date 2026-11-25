import json
import os
import shutil
import subprocess
import unittest


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP_JS_PATH = os.path.join(REPO_ROOT, "ui", "app.js")


class TestUiSecurity(unittest.TestCase):
    def test_app_js_has_no_inline_handlers_or_global_action_bridges(self):
        with open(APP_JS_PATH, "r", encoding="utf-8") as fh:
            source = fh.read()

        self.assertNotIn(
            "window.aetherTogglePause",
            source,
            msg="ui/app.js must not expose window.aetherTogglePause",
        )
        self.assertNotIn(
            "window.aetherDelete",
            source,
            msg="ui/app.js must not expose window.aetherDelete",
        )
        self.assertNotIn(
            "onclick=",
            source,
            msg="ui/app.js must not embed inline onclick= attributes",
        )

    def test_dom_xss_payloads_never_use_innerhtml_or_inject_elements(self):
        node_bin = shutil.which("node")
        if not node_bin:
            self.skipTest("node binary is not available in PATH")

        harness = r"""
const fs = require("fs");
const vm = require("vm");

const appJsPath = process.argv[1];
const appSource = fs.readFileSync(appJsPath, "utf8");

const innerHtmlWrites = [];
const createdElements = [];
const fetchCalls = [];
const alertCalls = [];
const consoleErrors = [];
const elementsById = new Map();
const docListeners = {};

class MockClassList {
  constructor(el) {
    this.el = el;
  }
  _tokens() {
    return (this.el._className || "").split(/\s+/).filter(Boolean);
  }
  _save(tokens) {
    this.el._className = tokens.join(" ");
  }
  add(...names) {
    const tokens = new Set(this._tokens());
    names.forEach((n) => tokens.add(n));
    this._save(Array.from(tokens));
  }
  remove(...names) {
    const removeSet = new Set(names);
    this._save(this._tokens().filter((t) => !removeSet.has(t)));
  }
  toggle(name, force) {
    const tokens = new Set(this._tokens());
    if (force === undefined) {
      if (tokens.has(name)) tokens.delete(name);
      else tokens.add(name);
    } else if (force) {
      tokens.add(name);
    } else {
      tokens.delete(name);
    }
    this._save(Array.from(tokens));
  }
  contains(name) {
    return this._tokens().includes(name);
  }
}

class MockElement {
  constructor(tagName, namespaceURI = null, id = null) {
    this.tagName = String(tagName).toLowerCase();
    this.namespaceURI = namespaceURI;
    this.id = id || "";
    this._className = "";
    this.classList = new MockClassList(this);
    this.children = [];
    this.attributes = {};
    this.style = {};
    this.dataset = {};
    this.value = "";
    this._textContent = "";
    this.listeners = {};
    this.onclick = null;
  }

  get className() {
    return this._className;
  }
  set className(val) {
    this._className = String(val);
  }

  get textContent() {
    return this._textContent;
  }
  set textContent(val) {
    this._textContent = val == null ? "" : String(val);
  }

  get innerHTML() {
    return "";
  }
  set innerHTML(val) {
    const str = val == null ? "" : String(val);
    innerHtmlWrites.push({ id: this.id, tagName: this.tagName, value: str });
    if (str !== "") {
      throw new Error(`Dynamic innerHTML write forbidden on <${this.tagName}#${this.id}>: ${str.slice(0, 80)}`);
    }
    this.children = [];
  }

  setAttribute(name, value) {
    this.attributes[String(name)] = String(value);
  }
  getAttribute(name) {
    return this.attributes[String(name)];
  }
  appendChild(child) {
    this.children.push(child);
    return child;
  }
  addEventListener(type, fn) {
    if (!this.listeners[type]) this.listeners[type] = [];
    this.listeners[type].push(fn);
  }
  dispatchEvent(type, eventObj = {}) {
    const evt = {
      type,
      target: this,
      stopPropagation: () => {},
      preventDefault: () => {},
      ...eventObj,
    };
    if (type === "click" && typeof this.onclick === "function") {
      this.onclick(evt);
    }
    for (const fn of this.listeners[type] || []) {
      fn(evt);
    }
  }
  click() {
    this.dispatchEvent("click");
  }
  focus() {}
}

function getOrCreateById(id) {
  if (!elementsById.has(id)) {
    elementsById.set(id, new MockElement("div", null, id));
  }
  return elementsById.get(id);
}

const dialButtons = ["obsidian", "cyber", "oled"].map((theme) => {
  const btn = new MockElement("button");
  btn.className = "dial-btn";
  btn.dataset = { setTheme: theme };
  return btn;
});

const mockDocument = {
  documentElement: new MockElement("html"),
  getElementById: (id) => getOrCreateById(id),
  querySelectorAll: (selector) => (selector === ".dial-btn" ? dialButtons : []),
  createElement: (tag) => {
    const el = new MockElement(tag, null);
    createdElements.push(el);
    return el;
  },
  createElementNS: (ns, tag) => {
    const el = new MockElement(tag, ns);
    createdElements.push(el);
    return el;
  },
  addEventListener: (type, fn) => {
    if (!docListeners[type]) docListeners[type] = [];
    docListeners[type].push(fn);
  },
};

let latestWs = null;
class MockWebSocket {
  constructor(url) {
    this.url = url;
    latestWs = this;
  }
  close() {}
}

const storage = new Map();
const sandbox = {
  document: mockDocument,
  localStorage: {
    getItem: (k) => (storage.has(k) ? storage.get(k) : null),
    setItem: (k, v) => storage.set(k, String(v)),
  },
  navigator: {},
  WebSocket: MockWebSocket,
  fetch: async (url, opts) => {
    fetchCalls.push({ url, method: (opts && opts.method) || "GET" });
    return { json: async () => ({ success: true }) };
  },
  confirm: () => true,
  alert: (msg) => {
    alertCalls.push(String(msg));
  },
  setTimeout: () => 0,
  console: {
    log: () => {},
    warn: () => {},
    error: (...args) => consoleErrors.push(args.map(String).join(" ")),
  },
  Math,
  Number,
  String,
  Boolean,
  Array,
  Object,
  Set,
  Map,
  JSON,
  Error,
  parseInt,
  parseFloat,
  encodeURIComponent,
};
sandbox.window = {
  location: { protocol: "http:", host: "127.0.0.1:8080" },
};
sandbox.globalThis = sandbox;

vm.runInNewContext(appSource, sandbox, { filename: appJsPath });

for (const fn of docListeners["DOMContentLoaded"] || []) {
  fn();
}

const maliciousTask = {
  id: "abc');alert(1);//",
  filename: "<img src=x onerror=alert(1)>'\"",
  url: "https://example.com/<svg/onload=alert(1)>",
  status: 'downloading" onclick="alert(1)',
  bytes_downloaded: 50,
  total_size: 100,
  progress: 50,
  speed_bps: 1024,
  eta_seconds: 1,
  connections: 1,
  supports_range: true,
  segments: [
    {
      id: 0,
      start_byte: 0,
      current_byte: 50,
      end_byte: 99,
      total: 100,
      progress: 50,
      speed_bps: 1024,
      status: 'downloading" onload="alert(1)',
    },
  ],
};

latestWs.onmessage({
  data: JSON.stringify({
    type: "init",
    download_dir: "/tmp/aether",
    downloads: [maliciousTask],
  }),
});

function walk(root, out = []) {
  out.push(root);
  for (const child of root.children || []) {
    walk(child, out);
  }
  return out;
}

const listNodes = walk(getOrCreateById("downloads-list"));
const segNodes = walk(getOrCreateById("insp-segments-grid"));
const allRendered = [...listNodes, ...segNodes];

const filenameEl = listNodes.find((n) => n.classList.contains("item-filename"));
const badgeEl = listNodes.find((n) => n.classList.contains("item-badge"));
const segBlockEl = segNodes.find((n) => n.classList.contains("segment-block"));
const inspFilenameText = getOrCreateById("insp-filename").textContent;
const inspUrlText = getOrCreateById("insp-url").textContent;

// Trigger click handlers on card buttons to confirm safe event listeners work
const actionButtons = listNodes.filter((n) => n.tagName === "button");
for (const btn of actionButtons) {
  btn.click();
}

// Verify normal allowlisted statuses render expected classes too
latestWs.onmessage({
  data: JSON.stringify({
    type: "task_added",
    task: {
      id: "safe-task-1",
      filename: "archive.tar.gz",
      url: "https://example.com/archive.tar.gz",
      status: "downloading",
      bytes_downloaded: 25,
      total_size: 100,
      progress: 25,
      speed_bps: 2048,
      eta_seconds: 2,
      connections: 2,
      supports_range: true,
      segments: [
        { id: 0, start_byte: 0, current_byte: 25, end_byte: 49, total: 50, progress: 50, speed_bps: 2048, status: "downloading" },
        { id: 1, start_byte: 50, current_byte: 100, end_byte: 99, total: 50, progress: 100, speed_bps: 0, status: "completed" },
      ],
    },
  }),
});

const safeListNodes = walk(getOrCreateById("downloads-list"));
const safeSegNodes = walk(getOrCreateById("insp-segments-grid"));
const safeBadgeEl = safeListNodes.find((n) => n.classList.contains("item-badge") && n.textContent === "downloading");
const safeSegBlocks = safeSegNodes.filter((n) => n.classList.contains("segment-block"));

// Verify empty state works cleanly as well
latestWs.onmessage({
  data: JSON.stringify({
    type: "init",
    download_dir: "/tmp/aether",
    downloads: [],
  }),
});
const emptyStateNodes = walk(getOrCreateById("downloads-list"));
const hasEmptyState = emptyStateNodes.some((n) => n.classList.contains("empty-state"));

const result = {
  consoleErrors,
  nonEmptyInnerHtmlWrites: innerHtmlWrites.filter((w) => w.value !== ""),
  createdImgCount: createdElements.filter((e) => e.tagName === "img").length,
  createdSvgTags: createdElements
    .filter((e) => e.tagName === "svg")
    .map((e) => ({ ns: e.namespaceURI, attrs: e.attributes })),
  inlineEventAttrs: allRendered
    .flatMap((n) => Object.keys(n.attributes).map((k) => ({ tag: n.tagName, attr: k })))
    .filter((x) => x.attr.toLowerCase().startsWith("on")),
  hasAetherTogglePause: typeof sandbox.window.aetherTogglePause !== "undefined",
  hasAetherDelete: typeof sandbox.window.aetherDelete !== "undefined",
  cardFilenameText: filenameEl ? filenameEl.textContent : null,
  inspFilenameText,
  inspUrlText,
  badgeClassName: badgeEl ? badgeEl.className : null,
  badgeText: badgeEl ? badgeEl.textContent : null,
  segBlockClassName: segBlockEl ? segBlockEl.className : null,
  safeBadgeClassName: safeBadgeEl ? safeBadgeEl.className : null,
  safeSegClasses: safeSegBlocks.map((b) => b.className),
  hasEmptyState,
  alertCalls,
  fetchCalls,
};

process.stdout.write(JSON.stringify(result));
"""

        proc = subprocess.run(
            [node_bin, "-e", harness, APP_JS_PATH],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            proc.returncode,
            0,
            msg=f"Node harness failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}",
        )

        data = json.loads(proc.stdout)
        self.assertEqual(data["consoleErrors"], [])
        self.assertEqual(data["nonEmptyInnerHtmlWrites"], [])
        self.assertEqual(data["createdImgCount"], 0)
        self.assertEqual(data["inlineEventAttrs"], [])
        self.assertFalse(data["hasAetherTogglePause"])
        self.assertFalse(data["hasAetherDelete"])
        self.assertEqual(data["alertCalls"], [])

        for svg in data["createdSvgTags"]:
            self.assertEqual(svg["ns"], "http://www.w3.org/2000/svg")

        expected_filename = "<img src=x onerror=alert(1)>'\""
        expected_url = "https://example.com/<svg/onload=alert(1)>"
        self.assertEqual(data["cardFilenameText"], expected_filename)
        self.assertEqual(data["inspFilenameText"], expected_filename)
        self.assertEqual(data["inspUrlText"], expected_url)

        self.assertEqual(data["badgeClassName"], "item-badge badge-initializing")
        self.assertEqual(data["badgeText"], 'downloading" onclick="alert(1)')
        self.assertEqual(data["segBlockClassName"], "segment-block")

        self.assertEqual(data["safeBadgeClassName"], "item-badge badge-downloading")
        self.assertEqual(
            data["safeSegClasses"],
            ["segment-block active", "segment-block completed"],
        )
        self.assertTrue(data["hasEmptyState"])
        self.assertGreaterEqual(len(data["fetchCalls"]), 2)


if __name__ == "__main__":
    unittest.main()
