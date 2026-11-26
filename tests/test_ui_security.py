import json
import os
import re
import shutil
import subprocess
import unittest
from html.parser import HTMLParser


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
UI_DIR = os.path.join(REPO_ROOT, "ui")
INDEX_HTML_PATH = os.path.join(UI_DIR, "index.html")
STYLE_CSS_PATH = os.path.join(UI_DIR, "style.css")
APP_JS_PATH = os.path.join(UI_DIR, "app.js")

VOID_HTML_TAGS = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"})


class _DomTreeParser(HTMLParser):
    """Lightweight HTML parser to extract real DOM nodes and attributes from ui/index.html."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = {"tag": "#document", "attrs": {}, "children": [], "text": ""}
        self._stack = [self.root]
        self.all_elements = []

    def handle_starttag(self, tag, attrs):
        tag_lower = tag.lower()
        attr_dict = {k: ("" if v is None else v) for k, v in attrs}
        node = {"tag": tag_lower, "attrs": attr_dict, "children": [], "text": ""}
        self._stack[-1]["children"].append(node)
        self.all_elements.append(node)
        if tag_lower not in VOID_HTML_TAGS:
            self._stack.append(node)

    def handle_startendtag(self, tag, attrs):
        tag_lower = tag.lower()
        attr_dict = {k: ("" if v is None else v) for k, v in attrs}
        node = {"tag": tag_lower, "attrs": attr_dict, "children": [], "text": ""}
        self._stack[-1]["children"].append(node)
        self.all_elements.append(node)

    def handle_endtag(self, tag):
        tag_lower = tag.lower()
        for idx in range(len(self._stack) - 1, 0, -1):
            if self._stack[idx]["tag"] == tag_lower:
                del self._stack[idx:]
                break

    def handle_data(self, data):
        if self._stack:
            self._stack[-1]["text"] += data


def _parse_index_html():
    with open(INDEX_HTML_PATH, "r", encoding="utf-8") as fh:
        html_source = fh.read()
    parser = _DomTreeParser()
    parser.feed(html_source)
    parser.close()
    html_node = next((c for c in parser.root["children"] if c["tag"] == "html"), None)
    return html_source, html_node, parser.all_elements


_NODE_HARNESS_PRELUDE = r"""
const fs = require("fs");
const vm = require("vm");

const payload = JSON.parse(fs.readFileSync(0, "utf8"));
const appSource = fs.readFileSync(payload.appJsPath, "utf8");

const innerHtmlWrites = [];
const createdElements = [];
const fetchCalls = [];
const alertCalls = [];
const confirmCalls = [];
const consoleErrors = [];
const docListeners = {};

function toCamelCase(str) {
  return str.replace(/-([a-z])/g, (_, ch) => ch.toUpperCase());
}

class MockClassList {
  constructor(el) {
    this.el = el;
  }
  _tokens() {
    return (this.el._className || "").split(/\s+/).filter(Boolean);
  }
  _save(tokens) {
    this.el._className = tokens.join(" ");
    this.el.attributes.class = this.el._className;
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

function matchesSimpleSelector(el, part) {
  let rem = part.trim();
  if (!rem) return false;
  const tagMatch = rem.match(/^[a-zA-Z0-9_-]+/);
  if (tagMatch) {
    if (el.tagName !== tagMatch[0].toLowerCase()) return false;
    rem = rem.slice(tagMatch[0].length);
  }
  while (rem.length > 0) {
    if (rem[0] === "#") {
      const m = rem.match(/^#([a-zA-Z0-9_-]+)/);
      if (!m || el.id !== m[1]) return false;
      rem = rem.slice(m[0].length);
    } else if (rem[0] === ".") {
      const m = rem.match(/^\.([a-zA-Z0-9_-]+)/);
      if (!m || !el.classList.contains(m[1])) return false;
      rem = rem.slice(m[0].length);
    } else if (rem[0] === "[") {
      const m = rem.match(/^\[([a-zA-Z0-9_-]+)(?:=["']?([^"'\]]*)["']?)?\]/);
      if (!m) return false;
      const attrName = m[1];
      const expected = m[2];
      const actual = attrName === "type" && el.type ? el.type : el.getAttribute(attrName);
      if (expected === undefined) {
        if (actual === undefined && !Object.prototype.hasOwnProperty.call(el.attributes, attrName)) {
          return false;
        }
      } else if (actual !== expected) {
        return false;
      }
      rem = rem.slice(m[0].length);
    } else {
      return false;
    }
  }
  return true;
}

function matchesSelector(el, selector) {
  const groups = String(selector).split(",");
  for (const group of groups) {
    const parts = group.trim().split(/\s+/).filter(Boolean);
    if (parts.length === 0) continue;
    if (!matchesSimpleSelector(el, parts[parts.length - 1])) continue;
    let cur = el.parentNode;
    let idx = parts.length - 2;
    while (idx >= 0 && cur) {
      if (matchesSimpleSelector(cur, parts[idx])) {
        idx -= 1;
      }
      cur = cur.parentNode;
    }
    if (idx < 0) return true;
  }
  return false;
}

let mockDocument = null;

class MockElement {
  constructor(tagName, namespaceURI = null, id = "") {
    this.tagName = String(tagName).toLowerCase();
    this.namespaceURI = namespaceURI;
    this.id = id || "";
    this._className = "";
    this.classList = new MockClassList(this);
    this.children = [];
    this.parentNode = null;
    this.attributes = {};
    this.style = {};
    this.dataset = {};
    this.value = "";
    this.type = "";
    this.checked = false;
    this.disabled = false;
    this.required = false;
    this.selected = false;
    this.hidden = false;
    this.title = "";
    this.placeholder = "";
    this._textContent = "";
    this.listeners = {};
    this.onclick = null;
  }

  get isConnected() {
    let cur = this;
    while (cur) {
      if (mockDocument && cur === mockDocument.documentElement) return true;
      cur = cur.parentNode;
    }
    return false;
  }

  get className() {
    return this._className;
  }
  set className(val) {
    this._className = val == null ? "" : String(val);
    this.attributes.class = this._className;
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
    for (const child of this.children) {
      child.parentNode = null;
    }
    this.children = [];
  }

  setAttribute(name, value) {
    const key = String(name);
    const val = String(value);
    this.attributes[key] = val;
    if (key === "id") this.id = val;
    else if (key === "class") this._className = val;
    else if (key === "type") this.type = val;
    else if (key === "hidden") this.hidden = true;
    else if (key === "title") this.title = val;
    else if (key === "value") this.value = val;
    else if (key.startsWith("data-")) {
      this.dataset[toCamelCase(key.slice(5))] = val;
    }
  }
  getAttribute(name) {
    return this.attributes[String(name)];
  }
  hasAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attributes, String(name));
  }
  removeAttribute(name) {
    const key = String(name);
    delete this.attributes[key];
    if (key === "hidden") this.hidden = false;
  }

  appendChild(child) {
    if (child.parentNode) {
      child.parentNode.removeChild(child);
    }
    child.parentNode = this;
    this.children.push(child);
    if (this.tagName === "select" && child.tagName === "option") {
      if (child.selected || (this.children.length === 1 && this.value === "")) {
        this.value = child.value;
      }
    }
    return child;
  }

  insertBefore(newNode, referenceNode) {
    if (!newNode) return newNode;
    if (newNode.parentNode) {
      newNode.parentNode.removeChild(newNode);
    }
    newNode.parentNode = this;
    if (!referenceNode) {
      this.children.push(newNode);
      return newNode;
    }
    const idx = this.children.indexOf(referenceNode);
    if (idx === -1) {
      this.children.push(newNode);
    } else {
      this.children.splice(idx, 0, newNode);
    }
    return newNode;
  }

  removeChild(child) {
    const idx = this.children.indexOf(child);
    if (idx !== -1) {
      this.children.splice(idx, 1);
      child.parentNode = null;
    }
    return child;
  }

  remove() {
    if (this.parentNode) {
      this.parentNode.removeChild(this);
    }
  }

  querySelectorAll(selector) {
    const results = [];
    const walk = (node) => {
      for (const child of node.children) {
        if (matchesSelector(child, selector)) results.push(child);
        walk(child);
      }
    };
    walk(this);
    return results;
  }

  querySelector(selector) {
    const queue = [...this.children];
    while (queue.length > 0) {
      const node = queue.shift();
      if (matchesSelector(node, selector)) return node;
      queue.unshift(...node.children);
    }
    return null;
  }

  closest(selector) {
    let cur = this;
    while (cur) {
      if (matchesSelector(cur, selector)) return cur;
      cur = cur.parentNode;
    }
    return null;
  }

  addEventListener(type, fn) {
    if (!this.listeners[type]) this.listeners[type] = [];
    this.listeners[type].push(fn);
  }

  dispatchEvent(typeOrEvent, eventObj = {}) {
    const type = typeof typeOrEvent === "string" ? typeOrEvent : typeOrEvent.type;
    const evt = {
      type,
      target: this,
      currentTarget: this,
      stopPropagation: () => {},
      preventDefault: () => {},
      ...(typeof typeOrEvent === "object" ? typeOrEvent : {}),
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

  focus() {
    if (mockDocument) {
      mockDocument.activeElement = this;
    }
  }

  blur() {
    if (mockDocument && mockDocument.activeElement === this) {
      mockDocument.activeElement = mockDocument.documentElement;
    }
  }
}

function hydrateDomNode(spec, ns = null) {
  const nextNs = spec.tag === "svg" ? "http://www.w3.org/2000/svg" : ns;
  const el = new MockElement(spec.tag, nextNs, spec.attrs.id || "");
  for (const [k, v] of Object.entries(spec.attrs || {})) {
    el.setAttribute(k, v);
    if (k === "selected") el.selected = true;
    if (k === "required") el.required = true;
    if (k === "disabled") el.disabled = true;
    if (k === "checked") el.checked = true;
  }
  if (spec.text) {
    el._textContent = spec.text.trim();
  }
  for (const childSpec of spec.children || []) {
    el.appendChild(hydrateDomNode(childSpec, nextNs));
  }
  return el;
}

const documentElement = hydrateDomNode(payload.domTree);

function findById(root, id) {
  if (root.id === id) return root;
  for (const child of root.children) {
    const found = findById(child, id);
    if (found) return found;
  }
  return null;
}

mockDocument = {
  documentElement,
  activeElement: documentElement,
  hidden: false,
  getElementById: (id) => findById(documentElement, String(id)),
  querySelector: (sel) => documentElement.querySelector(sel),
  querySelectorAll: (sel) => documentElement.querySelectorAll(sel),
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
  dispatchEvent: (type, eventObj = {}) => {
    const evt = {
      type,
      target: mockDocument,
      stopPropagation: () => {},
      preventDefault: () => {},
      ...eventObj,
    };
    for (const fn of docListeners[type] || []) {
      fn(evt);
    }
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

let fetchHandler = async (url, opts) => ({
  ok: true,
  json: async () => ({ success: true }),
});

const storage = new Map();
const sandbox = {
  document: mockDocument,
  localStorage: {
    getItem: (k) => (storage.has(k) ? storage.get(k) : null),
    setItem: (k, v) => storage.set(k, String(v)),
  },
  navigator: {},
  WebSocket: MockWebSocket,
  fetch: async (url, opts = {}) => {
    fetchCalls.push({
      url: String(url),
      method: opts.method || "GET",
      headers: opts.headers || {},
      body: opts.body || null,
    });
    return fetchHandler(url, opts);
  },
  confirm: (msg) => {
    confirmCalls.push(String(msg));
    return false;
  },
  alert: (msg) => {
    alertCalls.push(String(msg));
  },
  setTimeout: () => 0,
  clearTimeout: () => {},
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
  Promise,
  parseInt,
  parseFloat,
  isFinite,
  isNaN,
  encodeURIComponent,
};
sandbox.window = {
  location: { protocol: "http:", host: "127.0.0.1:8080" },
};
sandbox.globalThis = sandbox;

vm.runInNewContext(appSource, sandbox, { filename: payload.appJsPath });

for (const fn of docListeners["DOMContentLoaded"] || []) {
  fn();
}

function sendWs(msg) {
  latestWs.onmessage({ data: JSON.stringify(msg) });
}

async function flushMicrotasks() {
  for (let i = 0; i < 6; i += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
}
"""


class TestUiSecurity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node_bin = shutil.which("node")
        cls.html_source, cls.dom_tree, cls.html_elements = _parse_index_html()
        with open(STYLE_CSS_PATH, "r", encoding="utf-8") as fh:
            cls.css_source = fh.read()
        with open(APP_JS_PATH, "r", encoding="utf-8") as fh:
            cls.app_source = fh.read()

    def _run_node_test(self, test_js):
        self.assertIsNotNone(self.node_bin, "node binary must be available in PATH")
        full_script = (
            _NODE_HARNESS_PRELUDE
            + "\n(async () => {\n"
            + test_js
            + "\n})().catch((err) => { console.error(err && err.stack ? err.stack : String(err)); process.exit(1); });\n"
        )
        stdin_payload = json.dumps(
            {
                "appJsPath": APP_JS_PATH,
                "domTree": self.dom_tree,
            }
        )
        proc = subprocess.run(
            [self.node_bin, "-e", full_script],
            input=stdin_payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            check=False,
        )
        self.assertEqual(
            proc.returncode,
            0,
            msg=f"Node harness failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}",
        )
        return json.loads(proc.stdout)

    def test_static_security_accessibility_and_redesign_invariants(self):
        # Zero inline onclick= or inline on*= event attributes in HTML/JS
        self.assertNotIn("onclick=", self.app_source)
        self.assertNotIn("onclick=", self.html_source)
        for el in self.html_elements:
            for attr_name in el["attrs"]:
                self.assertFalse(
                    attr_name.lower().startswith("on"),
                    msg=f"Inline event attribute {attr_name!r} found on <{el['tag']}>",
                )

        # Zero global action bridges or native alert/confirm dialogs
        self.assertNotIn("window.aetherTogglePause", self.app_source)
        self.assertNotIn("window.aetherDelete", self.app_source)
        self.assertIsNone(re.search(r"\b(?:alert|confirm)\s*\(", self.app_source))
        self.assertIsNone(re.search(r"\b(?:alert|confirm)\s*\(", self.html_source))

        # Removed Phase 1 GPU-heavy ambient/infinite animations & reduced-motion support
        self.assertNotIn(".ambient-glow", self.html_source)
        self.assertNotIn(".ambient-glow", self.css_source)
        self.assertNotIn("@keyframes spin", self.css_source)
        self.assertNotIn("pulse-dot", self.css_source)
        self.assertIn("@media (prefers-reduced-motion: reduce)", self.css_source)

        # Both modals must expose accessible dialog semantics
        by_id = {
            el["attrs"]["id"]: el
            for el in self.html_elements
            if "id" in el["attrs"]
        }
        for modal_id in ("modal-new-download", "modal-delete-download"):
            self.assertIn(modal_id, by_id)
            attrs = by_id[modal_id]["attrs"]
            self.assertEqual(attrs.get("role"), "dialog", msg=f"{modal_id} missing role=dialog")
            self.assertEqual(attrs.get("aria-modal"), "true", msg=f"{modal_id} missing aria-modal=true")

        # Zero hardcoded ~/Descargas in HTML <option> tags or index.html
        self.assertNotIn("~/Descargas", self.html_source)
        for el in self.html_elements:
            if el["tag"] == "option":
                self.assertNotIn("~/Descargas", el["attrs"].get("value", ""))
                self.assertNotIn("~/Descargas", el["text"])

        # Static CSS checks: Cyber segment status specificity (0,3,0) and modal viewport fit
        css_no_comments = re.sub(r"/\*.*?\*/", "", self.css_source, flags=re.DOTALL)

        def _extract_rule_body(selector):
            pattern = re.compile(
                rf"(?:^|\}})\s*{re.escape(selector)}\s*\{{([^}}]*)\}}",
            )
            match = pattern.search(css_no_comments)
            self.assertIsNotNone(match, msg=f"Missing CSS rule for {selector!r}")
            return match.group(1), match.start()

        with self.subTest("cyber_segment_band_specificity"):
            cyber_base_body, cyber_base_pos = _extract_rule_body('[data-theme="cyber"] .segment-band-cell')
            cyber_base_border = re.search(r"border-color\s*:\s*([^;]+);", cyber_base_body)
            self.assertIsNotNone(cyber_base_border)

            cyber_paused_body, cyber_paused_pos = _extract_rule_body(
                '[data-theme="cyber"] .segment-band-cell.paused'
            )
            self.assertGreater(cyber_paused_pos, cyber_base_pos)
            cyber_paused_border = re.search(r"border-color\s*:\s*([^;]+);", cyber_paused_body)
            self.assertIsNotNone(cyber_paused_border, msg="Cyber paused segment rule must set border-color")
            self.assertNotEqual(
                cyber_paused_border.group(1).strip(),
                cyber_base_border.group(1).strip(),
            )
            self.assertRegex(cyber_paused_border.group(1), r"(?:245,\s*158,\s*11|--accent-amber|#f59e0b)")

            cyber_error_body, cyber_error_pos = _extract_rule_body(
                '[data-theme="cyber"] .segment-band-cell.error'
            )
            self.assertGreater(cyber_error_pos, cyber_base_pos)
            cyber_error_border = re.search(r"border-color\s*:\s*([^;]+);", cyber_error_body)
            self.assertIsNotNone(cyber_error_border, msg="Cyber error segment rule must set border-color")
            self.assertNotEqual(
                cyber_error_border.group(1).strip(),
                cyber_base_border.group(1).strip(),
            )
            self.assertNotEqual(
                cyber_error_border.group(1).strip(),
                cyber_paused_border.group(1).strip(),
            )
            self.assertRegex(cyber_error_border.group(1), r"(?:239,\s*68,\s*68|--accent-danger|#ef4444)")

        with self.subTest("modal_glass_card_viewport_fit"):
            modal_card_body, _ = _extract_rule_body(".modal-glass-card")
            self.assertRegex(
                modal_card_body,
                r"max-height\s*:\s*calc\(\s*100vh\s*-\s*2\.5rem\s*\)\s*;",
                msg=".modal-glass-card must constrain max-height to calc(100vh - 2.5rem)",
            )
            self.assertRegex(
                modal_card_body,
                r"overflow-y\s*:\s*auto\s*;",
                msg=".modal-glass-card must enable vertical scrolling when viewport is short",
            )

    def test_dom_xss_invariance_safe_percent_status_and_id_encoding(self):
        test_js = r"""
const maliciousTask = {
  id: "abc');alert(1);//",
  filename: "<img src=x onerror=alert(1)>'\"",
  url: "https://example.com/<svg/onload=alert(1)>",
  status: 'downloading" onclick="alert(1)',
  bytes_downloaded: 50,
  total_size: 100,
  progress: "50%;background:red",
  speed_bps: 1024,
  eta_seconds: 1,
  connections: 4,
  supports_range: true,
  segments: [
    { id: 0, start_byte: 0, current_byte: 0, end_byte: 24, total: 25, progress: "NaN", speed_bps: 0, status: 'downloading" onload="alert(1)' },
    { id: 1, start_byte: 25, current_byte: 25, end_byte: 49, total: 25, progress: -5, speed_bps: 0, status: "paused" },
    { id: 2, start_byte: 50, current_byte: 75, end_byte: 74, total: 25, progress: 150, speed_bps: 1024, status: "completed" },
    { id: 3, start_byte: 75, current_byte: 75, end_byte: 99, total: 25, progress: "50%;background:red", speed_bps: 0, status: "initializing" },
  ],
};

const clampTasks = [
  { ...maliciousTask, id: "clamp-nan", filename: "nan.bin", progress: NaN, segments: [] },
  { ...maliciousTask, id: "clamp-neg", filename: "neg.bin", progress: -5, segments: [] },
  { ...maliciousTask, id: "clamp-over", filename: "over.bin", progress: 150, segments: [] },
  { ...maliciousTask, id: "clamp-str", filename: "str.bin", progress: "50%;background:red", segments: [] },
];

const maliciousErrorTask = {
  id: "err/id?x=1",
  filename: "<svg onload=alert(1)>broken.iso",
  url: "https://example.com/broken.iso",
  status: "error",
  error_message: "<img src=x onerror=alert(1)>Error 403",
  bytes_downloaded: 0,
  total_size: 100,
  progress: -5,
  speed_bps: 0,
  eta_seconds: null,
  connections: 1,
  supports_range: false,
  segments: [],
};

const maliciousFolders = [
  { label: "<img src=x onerror=alert(1)>Descargas", path: "/tmp/<svg/onload=alert(1)>" },
];

sendWs({
  type: "init",
  download_dir: "/tmp/aether",
  quick_folders: maliciousFolders,
  downloads: [maliciousTask, ...clampTasks, maliciousErrorTask],
});

const listEl = mockDocument.getElementById("downloads-list");
const cards = listEl.querySelectorAll(".download-item-card");
const malCard = cards[0];

// Expand drawer on malicious task and trigger pause/limit/delete actions to inspect fetch URL encoding
malCard.querySelector(".btn-details").click();
const malDrawerUrl = malCard.querySelector(".row-drawer-url").textContent;
const malSegCells = malCard.querySelectorAll(".segment-band-cell");
const malSegFills = malCard.querySelectorAll(".segment-band-fill");

// First icon button in .item-actions is pause/resume
malCard.querySelector(".item-actions .btn-icon").click();
const malLimitSelect = malCard.querySelector(".row-limit-select");
malLimitSelect.value = "1048576";
malLimitSelect.dispatchEvent("change");
await flushMicrotasks();

// Also verify delete modal + DELETE URL encoding
malCard.querySelector(".btn-danger").click();
mockDocument.getElementById("btn-confirm-delete").click();
await flushMicrotasks();

const allNodes = [mockDocument.documentElement];
for (let i = 0; i < allNodes.length; i += 1) {
  allNodes.push(...allNodes[i].children);
}

const quickFolderOptions = mockDocument.getElementById("quick-folders").querySelectorAll("option");
const errBanner = cards[5].querySelector(".item-error-banner");

process.stdout.write(JSON.stringify({
  consoleErrors,
  nonEmptyInnerHtmlWrites: innerHtmlWrites.filter((w) => w.value !== ""),
  createdImgCount: createdElements.filter((e) => e.tagName === "img").length,
  createdSvgNamespaces: createdElements.filter((e) => e.tagName === "svg").map((e) => e.namespaceURI),
  inlineEventAttrs: allNodes
    .flatMap((n) => Object.keys(n.attributes).map((k) => ({ tag: n.tagName, attr: k })))
    .filter((x) => x.attr.toLowerCase().startsWith("on")),
  hasAetherTogglePause: typeof sandbox.window.aetherTogglePause !== "undefined",
  hasAetherDelete: typeof sandbox.window.aetherDelete !== "undefined",
  alertCalls,
  confirmCalls,
  malFilename: malCard.querySelector(".item-filename").textContent,
  malBadgeClass: malCard.querySelector(".item-badge").className,
  malBadgeText: malCard.querySelector(".item-badge").textContent,
  malDrawerUrl,
  malSegClasses: malSegCells.map((c) => c.className),
  malSegWidths: malSegFills.map((f) => f.style.width),
  malErrorBannerHidden: malCard.querySelector(".item-error-banner").hidden,
  malErrorBannerText: malCard.querySelector(".item-error-banner").textContent,
  clampWidths: clampTasks.map((_, idx) => cards[idx + 1].querySelector(".item-progress-fill").style.width),
  clampProgressTexts: clampTasks.map((_, idx) => cards[idx + 1].querySelector(".item-meta").children[2].textContent),
  errorBannerText: errBanner.textContent,
  errorBannerHidden: errBanner.hidden,
  quickFolderOption: quickFolderOptions[1]
    ? { value: quickFolderOptions[1].value, textContent: quickFolderOptions[1].textContent }
    : null,
  fetchCalls,
}));
"""
        data = self._run_node_test(test_js)
        self.assertEqual(data["consoleErrors"], [])
        self.assertEqual(data["nonEmptyInnerHtmlWrites"], [])
        self.assertEqual(data["createdImgCount"], 0)
        self.assertTrue(all(ns == "http://www.w3.org/2000/svg" for ns in data["createdSvgNamespaces"]))
        self.assertEqual(data["inlineEventAttrs"], [])
        self.assertFalse(data["hasAetherTogglePause"])
        self.assertFalse(data["hasAetherDelete"])
        self.assertEqual(data["alertCalls"], [])
        self.assertEqual(data["confirmCalls"], [])

        expected_filename = "<img src=x onerror=alert(1)>'\""
        expected_url = "https://example.com/<svg/onload=alert(1)>"
        self.assertEqual(data["malFilename"], expected_filename)
        self.assertEqual(data["malBadgeClass"], "item-badge badge-initializing")
        self.assertEqual(data["malBadgeText"], 'downloading" onclick="alert(1)')
        self.assertEqual(data["malDrawerUrl"], expected_url)

        self.assertEqual(
            data["malSegClasses"],
            [
                "segment-band-cell",
                "segment-band-cell paused",
                "segment-band-cell completed",
                "segment-band-cell",
            ],
        )
        self.assertEqual(data["malSegWidths"], ["0%", "0%", "100%", "0%"])
        self.assertTrue(data["malErrorBannerHidden"])
        self.assertEqual(data["malErrorBannerText"], "")
        self.assertEqual(data["clampWidths"], ["0%", "0%", "100%", "0%"])
        self.assertEqual(data["clampProgressTexts"], ["0%", "0%", "100%", "0%"])

        self.assertFalse(data["errorBannerHidden"])
        self.assertEqual(data["errorBannerText"], "<img src=x onerror=alert(1)>Error 403")
        self.assertEqual(
            data["quickFolderOption"],
            {
                "value": "/tmp/<svg/onload=alert(1)>",
                "textContent": "<img src=x onerror=alert(1)>Descargas",
            },
        )

        encoded_id = "abc')%3Balert(1)%3B%2F%2F"
        fetch_urls = [c["url"] for c in data["fetchCalls"]]
        self.assertIn(f"/api/downloads/{encoded_id}/resume", fetch_urls)
        self.assertIn(f"/api/downloads/{encoded_id}/limit", fetch_urls)
        self.assertIn(f"/api/downloads/{encoded_id}?delete_file=false", fetch_urls)

    def test_incremental_dom_identity_preserves_card_references(self):
        test_js = r"""
const baseTask = {
  id: "inc-task-1",
  filename: "ubuntu-24.04.iso",
  url: "https://example.com/ubuntu.iso",
  status: "downloading",
  bytes_downloaded: 100 * 1024 * 1024,
  total_size: 1000 * 1024 * 1024,
  progress: 10,
  speed_bps: 1048576,
  eta_seconds: 900,
  connections: 8,
  supports_range: true,
  segments: [
    { id: 0, start_byte: 0, current_byte: 50, end_byte: 499, total: 500, progress: 10, speed_bps: 524288, status: "downloading" },
    { id: 1, start_byte: 500, current_byte: 550, end_byte: 999, total: 500, progress: 10, speed_bps: 524288, status: "downloading" },
  ],
};

sendWs({ type: "init", download_dir: "/tmp/aether", downloads: [baseTask] });
const listEl = mockDocument.getElementById("downloads-list");
const cardBefore = listEl.children[0];

const updates = [
  { progress: 35, speed_bps: 2097152, bytes_downloaded: 350 * 1024 * 1024, eta_seconds: 300 },
  { progress: 65, speed_bps: 3145728, bytes_downloaded: 650 * 1024 * 1024, eta_seconds: 120 },
  { progress: 90, speed_bps: 4194304, bytes_downloaded: 900 * 1024 * 1024, eta_seconds: 25 },
];

for (const patch of updates) {
  sendWs({
    type: "task_progress",
    task: { ...baseTask, ...patch },
  });
}

const cardAfter = listEl.children[0];
const metaSpans = cardAfter.querySelector(".item-meta").children;

process.stdout.write(JSON.stringify({
  consoleErrors,
  cardCount: listEl.children.length,
  sameCardReference: cardAfter === cardBefore,
  isDownloadCard: cardAfter.classList.contains("download-item-card"),
  progressWidth: cardAfter.querySelector(".item-progress-fill").style.width,
  speedText: metaSpans[1].textContent,
  progressText: metaSpans[2].textContent,
  globalSpeed: mockDocument.getElementById("total-speed").textContent,
  globalSpeedUnit: mockDocument.getElementById("total-speed-unit").textContent,
}));
"""
        data = self._run_node_test(test_js)
        self.assertEqual(data["consoleErrors"], [])
        self.assertEqual(data["cardCount"], 1)
        self.assertTrue(data["isDownloadCard"])
        self.assertTrue(data["sameCardReference"])
        self.assertEqual(data["progressWidth"], "90%")
        self.assertEqual(data["speedText"], "4.00 MB/s")
        self.assertEqual(data["progressText"], "90%")
        self.assertEqual(data["globalSpeed"], "4.00")
        self.assertEqual(data["globalSpeedUnit"], "MB/s")

    def test_inline_drawer_segment_band_and_live_limit_focus_guard(self):
        test_js = r"""
const task = {
  id: "drawer-task-1",
  filename: "model-weights.safetensors",
  url: "https://cdn.example.com/model-weights.safetensors",
  status: "downloading",
  speed_limit_bps: 0,
  bytes_downloaded: 500,
  total_size: 1000,
  progress: 50,
  speed_bps: 2097152,
  eta_seconds: 10,
  connections: 3,
  supports_range: true,
  segments: [
    { id: 0, start_byte: 0, current_byte: 250, end_byte: 399, total: 400, progress: 62.5, speed_bps: 1048576, status: "downloading" },
    { id: 1, start_byte: 400, current_byte: 700, end_byte: 699, total: 300, progress: 100, speed_bps: 0, status: "completed" },
    { id: 2, start_byte: 700, current_byte: 750, end_byte: 999, total: 300, progress: 16.7, speed_bps: 1048576, status: "downloading" },
  ],
};

sendWs({ type: "init", download_dir: "/tmp/aether", downloads: [task] });
const card = mockDocument.getElementById("downloads-list").children[0];
const drawer = card.querySelector(".row-details-drawer");
const detailsBtn = card.querySelector(".btn-details");
const limitSelect = card.querySelector(".row-limit-select");

const initiallyHidden = drawer.hidden === true;
const initialExpandedAttr = detailsBtn.getAttribute("aria-expanded");

// Expand inline drawer
detailsBtn.click();
const expandedHidden = drawer.hidden === false;
const expandedAttr = detailsBtn.getAttribute("aria-expanded");
const segmentCellCount = drawer.querySelectorAll(".segment-band-cell").length;
const segmentBandFills = drawer.querySelectorAll(".segment-band-fill").map((f) => f.style.width);

// Change live limit select -> POST /api/downloads/<id>/limit
limitSelect.value = "5242880";
limitSelect.dispatchEvent("change");
await flushMicrotasks();

// Hold focus on .row-limit-select so task_progress cannot clobber user selection
limitSelect.focus();
limitSelect.value = "10485760";
sendWs({
  type: "task_progress",
  task: { ...task, progress: 60, speed_limit_bps: 1048576 },
});
const valueWhileFocused = limitSelect.value;

// Release focus, next task_progress syncs backend speed_limit_bps
limitSelect.blur();
sendWs({
  type: "task_progress",
  task: { ...task, progress: 70, speed_limit_bps: 1048576 },
});
const valueAfterBlur = limitSelect.value;

process.stdout.write(JSON.stringify({
  consoleErrors,
  initiallyHidden,
  initialExpandedAttr,
  expandedHidden,
  expandedAttr,
  segmentCellCount,
  segmentBandFills,
  valueWhileFocused,
  valueAfterBlur,
  limitCall: fetchCalls.find((c) => c.url === "/api/downloads/drawer-task-1/limit") || null,
}));
"""
        data = self._run_node_test(test_js)
        self.assertEqual(data["consoleErrors"], [])
        self.assertTrue(data["initiallyHidden"])
        self.assertEqual(data["initialExpandedAttr"], "false")
        self.assertTrue(data["expandedHidden"])
        self.assertEqual(data["expandedAttr"], "true")
        self.assertEqual(data["segmentCellCount"], 3)
        self.assertEqual(data["segmentBandFills"], ["62.5%", "100%", "16.7%"])
        self.assertEqual(data["valueWhileFocused"], "10485760")
        self.assertEqual(data["valueAfterBlur"], "1048576")

        limit_call = data["limitCall"]
        self.assertIsNotNone(limit_call)
        self.assertEqual(limit_call["method"], "POST")
        self.assertEqual(limit_call["headers"].get("Content-Type"), "application/json")
        self.assertEqual(json.loads(limit_call["body"]), {"limit_bps": 5242880})

    def test_error_banner_delete_modal_escape_quick_download_open_folder_and_visibility(self):
        test_js = r"""
// 1. Empty state -> quick-download, quick_folders, open-folder
sendWs({
  type: "init",
  download_dir: "/tmp/aether-dl",
  quick_folders: [
    { label: "Descargas", path: "/tmp/aether-dl" },
    { label: "Videos", path: "/tmp/aether-videos" },
  ],
  downloads: [],
});

const listEl = mockDocument.getElementById("downloads-list");
const quickCard = listEl.querySelector(".quick-empty-card");
const quickForm = mockDocument.getElementById("form-quick-download");
const quickFoldersModalOpts = mockDocument.getElementById("quick-folders").querySelectorAll("option").map((o) => ({
  value: o.value,
  text: o.textContent,
}));
const quickFoldersInlineOpts = mockDocument.getElementById("quick-folders-inline").querySelectorAll("option").map((o) => ({
  value: o.value,
  text: o.textContent,
}));

mockDocument.getElementById("quick-url").value = "https://example.com/distro.iso";
quickForm.dispatchEvent("submit");
await flushMicrotasks();

mockDocument.getElementById("btn-open-folder").click();
await flushMicrotasks();

// 2. Task in error state + hidden sparkline deferral + delete modal + Escape
const sparkBeforeHidden = mockDocument.getElementById("sparkline-line").getAttribute("d");
mockDocument.hidden = true;

const errTask = {
  id: "task-err-1",
  filename: "broken.tar.zst",
  url: "https://example.com/broken.tar.zst",
  status: "error",
  error_message: "404 Not Found on origin server",
  bytes_downloaded: 0,
  total_size: 1000,
  progress: 0,
  speed_bps: 0,
  eta_seconds: null,
  connections: 0,
  supports_range: true,
  segments: [],
};

sendWs({ type: "init", download_dir: "/tmp/aether-dl", downloads: [errTask] });
const errCard = listEl.children[0];
const errBanner = errCard.querySelector(".item-error-banner");
const errBannerText = errBanner.textContent;
const errBannerVisible = errBanner.hidden === false && errBanner.style.display === "";

// Emit task_progress while document.hidden = true -> sparkline must NOT be rewritten yet
sendWs({
  type: "task_progress",
  task: { ...errTask, status: "downloading", error_message: "", speed_bps: 10485760, connections: 8, progress: 25 },
});
const sparkWhileHidden = mockDocument.getElementById("sparkline-line").getAttribute("d");

// Restore visibility and fire visibilitychange -> sparkline updates now
mockDocument.hidden = false;
mockDocument.dispatchEvent("visibilitychange");
const sparkAfterVisible = mockDocument.getElementById("sparkline-line").getAttribute("d");

// Open new-download modal and close via Escape
const modalNew = mockDocument.getElementById("modal-new-download");
mockDocument.getElementById("btn-open-modal").click();
const newModalOpened = modalNew.classList.contains("open");
mockDocument.dispatchEvent("keydown", { key: "Escape" });
const newModalClosedByEsc = !modalNew.classList.contains("open");

// Open delete modal, close via Escape (no DELETE fetch), reopen, check delete_file and confirm
const modalDel = mockDocument.getElementById("modal-delete-download");
errCard.querySelector(".btn-danger").click();
const delModalOpened = modalDel.classList.contains("open");
mockDocument.dispatchEvent("keydown", { key: "Escape" });
const delModalClosedByEsc = !modalDel.classList.contains("open");
const deleteCountAfterEsc = fetchCalls.filter((c) => c.method === "DELETE").length;

errCard.querySelector(".btn-danger").click();
mockDocument.getElementById("checkbox-delete-file").checked = true;
mockDocument.getElementById("btn-confirm-delete").click();
await flushMicrotasks();

process.stdout.write(JSON.stringify({
  consoleErrors,
  hasQuickCard: Boolean(quickCard),
  hasQuickForm: Boolean(quickForm),
  quickFoldersModalOpts,
  quickFoldersInlineOpts,
  errBannerVisible,
  errBannerText,
  sparkBeforeHidden,
  sparkWhileHidden,
  sparkAfterVisible,
  newModalOpened,
  newModalClosedByEsc,
  delModalOpened,
  delModalClosedByEsc,
  deleteCountAfterEsc,
  delModalClosedAfterConfirm: !modalDel.classList.contains("open"),
  fetchCalls,
}));
"""
        data = self._run_node_test(test_js)
        self.assertEqual(data["consoleErrors"], [])
        self.assertTrue(data["hasQuickCard"])
        self.assertTrue(data["hasQuickForm"])

        expected_options = [
            {"value": "", "text": "Rutas rápidas..."},
            {"value": "/tmp/aether-dl", "text": "Descargas"},
            {"value": "/tmp/aether-videos", "text": "Videos"},
        ]
        self.assertEqual(data["quickFoldersModalOpts"], expected_options)
        self.assertEqual(data["quickFoldersInlineOpts"], expected_options)

        post_dl = next((c for c in data["fetchCalls"] if c["url"] == "/api/downloads"), None)
        self.assertIsNotNone(post_dl)
        self.assertEqual(post_dl["method"], "POST")
        self.assertEqual(post_dl["headers"].get("Content-Type"), "application/json")
        self.assertEqual(json.loads(post_dl["body"])["url"], "https://example.com/distro.iso")

        post_folder = next((c for c in data["fetchCalls"] if c["url"] == "/api/system/open-folder"), None)
        self.assertIsNotNone(post_folder)
        self.assertEqual(post_folder["method"], "POST")
        self.assertEqual(json.loads(post_folder["body"]), {"dir": "/tmp/aether-dl"})

        self.assertTrue(data["errBannerVisible"])
        self.assertEqual(data["errBannerText"], "404 Not Found on origin server")

        self.assertEqual(data["sparkWhileHidden"], data["sparkBeforeHidden"])
        self.assertNotEqual(data["sparkAfterVisible"], data["sparkWhileHidden"])

        self.assertTrue(data["newModalOpened"])
        self.assertTrue(data["newModalClosedByEsc"])
        self.assertTrue(data["delModalOpened"])
        self.assertTrue(data["delModalClosedByEsc"])
        self.assertEqual(data["deleteCountAfterEsc"], 0)
        self.assertTrue(data["delModalClosedAfterConfirm"])

        del_call = next((c for c in data["fetchCalls"] if c["method"] == "DELETE"), None)
        self.assertIsNotNone(del_call)
        self.assertEqual(del_call["url"], "/api/downloads/task-err-1?delete_file=true")

    def test_live_limit_select_reverts_on_network_failure(self):
        test_js = r"""
const task = {
  id: "net-err-task-1",
  filename: "archive.tar.gz",
  url: "https://example.com/archive.tar.gz",
  status: "downloading",
  speed_limit_bps: 1048576,
  bytes_downloaded: 100,
  total_size: 1000,
  progress: 10,
  speed_bps: 524288,
  eta_seconds: 2,
  connections: 4,
  supports_range: true,
  segments: [],
};

sendWs({ type: "init", download_dir: "/tmp/aether", downloads: [task] });
const card = mockDocument.getElementById("downloads-list").children[0];
const limitSelect = card.querySelector(".row-limit-select");
const initialValue = limitSelect.value;

fetchHandler = async () => {
  throw new Error("Network down");
};

limitSelect.focus();
limitSelect.value = "5242880";
limitSelect.dispatchEvent("change");
await flushMicrotasks();

const errorToasts = mockDocument
  .getElementById("toast-container")
  .querySelectorAll(".toast-error")
  .map((t) => ({ role: t.getAttribute("role"), text: t.textContent }));

process.stdout.write(JSON.stringify({
  consoleErrors,
  initialValue,
  revertedValue: limitSelect.value,
  errorToasts,
}));
"""
        data = self._run_node_test(test_js)
        self.assertEqual(data["consoleErrors"], [])
        self.assertEqual(data["initialValue"], "1048576")
        self.assertEqual(data["revertedValue"], "1048576")
        self.assertEqual(len(data["errorToasts"]), 1)
        self.assertEqual(data["errorToasts"][0]["role"], "alert")
        self.assertIn("Network down", data["errorToasts"][0]["text"])


if __name__ == "__main__":
    unittest.main()
