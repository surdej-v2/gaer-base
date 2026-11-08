// Shared state for the demo shop (demo.html), the simulated backend (admin.html) and the labels
// (etiketter.html). Everything lives in this browser's localStorage; nothing is sent anywhere.
// Mirrors the real tables: shop_products, shop_orders and the audit log.
(function () {
  "use strict";
  var KEY = "gaer.shopdemo.v2";
  var CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789";

  function seed() {
    var now = new Date().toISOString();
    var s = { version: 2, seq: 0, productSeq: 3, products: {}, orders: {}, audit: [] };
    [["aebler", 1, "Æbler, 1 kg", "Ingrid Marie fra haven. Tag en pose fra kurven.", 2500, 12],
     ["honning", 2, "Honning, 450 g", "Sommerhonning fra egne bistader.", 7500, 4],
     ["aeg", 3, "Friske æg, 10 stk", "", 3500, null]].forEach(function (p) {
      s.products[p[0]] = { id: p[1], code: p[0], name: p[2], description: p[3], price: p[4], stock: p[5], active: true, created_at: now };
    });
    return s;
  }

  function load() {
    try {
      var s = JSON.parse(localStorage.getItem(KEY));
      if (s && s.version === 2) return s;
    } catch (e) { /* fall through */ }
    return seed();
  }

  var state = load();
  var listeners = [];

  function save() {
    try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) { /* private mode: lives until reload */ }
    listeners.forEach(function (fn) { fn(); });
  }

  // Another tab (the demo shop) changed something: reload and tell the page.
  window.addEventListener("storage", function (ev) {
    if (ev.key !== KEY) return;
    state = load();
    listeners.forEach(function (fn) { fn(); });
  });

  function audit(action, table, rowId, before, after, actor) {
    state.audit.unshift({ ts: new Date().toISOString(), action: action, table: table, row_id: rowId,
                          actor: actor || "system", before: before || null, after: after || null });
    state.audit = state.audit.slice(0, 200);
  }

  function changed(before, after) {
    var b = {}, a = {};
    Object.keys(after).forEach(function (k) { if (before[k] !== after[k]) { b[k] = before[k]; a[k] = after[k]; } });
    return [b, a];
  }

  function newCode() {
    var c;
    do {
      var bytes = new Uint8Array(8);
      crypto.getRandomValues(bytes);
      c = Array.prototype.map.call(bytes, function (x) { return CODE_ALPHABET[x % CODE_ALPHABET.length]; }).join("");
    } while (state.products[c]);
    return c;
  }

  function token() {
    var b = new Uint8Array(16);
    crypto.getRandomValues(b);
    return Array.prototype.map.call(b, function (x) { return ("0" + x.toString(16)).slice(-2); }).join("");
  }

  var FIELDS = ["name", "description", "price", "stock", "active"];
  function pick(o) { var r = {}; FIELDS.forEach(function (k) { r[k] = o[k]; }); return r; }

  window.GaerStore = {
    subscribe: function (fn) { listeners.push(fn); },
    products: function () {
      return Object.keys(state.products).map(function (k) { return state.products[k]; })
        .sort(function (a, b) { return (b.active - a.active) || a.name.localeCompare(b.name, "da"); });
    },
    product: function (code) { return state.products[code] || null; },
    orders: function () {
      return Object.keys(state.orders).map(function (k) { return Object.assign({ token: k }, state.orders[k]); })
        .sort(function (a, b) { return b.id - a.id; });
    },
    order: function (t) { return state.orders[t] || null; },
    auditLog: function () { return state.audit.slice(); },

    addProduct: function (data) {
      var code = newCode();
      var p = Object.assign(pick(data), { id: ++state.productSeq, code: code, created_at: new Date().toISOString() });
      state.products[code] = p;
      audit("create", "shop_products", p.id, null, Object.assign({ code: code }, pick(p)), "admin");
      save();
      return p;
    },
    updateProduct: function (code, data) {
      var p = state.products[code];
      if (!p) return;
      var ba = changed(pick(p), pick(data));
      Object.assign(p, pick(data));
      if (Object.keys(ba[1]).length) audit("update", "shop_products", p.id, ba[0], ba[1], "admin");
      save();
    },
    deleteProduct: function (code) {
      var p = state.products[code];
      if (!p) return;
      delete state.products[code];
      audit("delete", "shop_products", p.id, { code: code, name: p.name, price: p.price }, null, "admin");
      save();
    },

    // Buyer side (demo.html)
    stock: function (code) { var p = state.products[code]; return p ? p.stock : null; },
    createOrder: function (code, qty) {
      var p = state.products[code];
      var t = token();
      state.orders[t] = { id: ++state.seq, code: code, name: p.name, qty: qty, amount: p.price * qty, status: "pending",
                          created_at: new Date().toISOString(), paid_at: null, fulfilled_at: null };
      audit("create", "shop_orders", state.seq, null, { product_id: p.id, quantity: qty, amount: p.price * qty, provider: "demo" });
      save();
      return t;
    },
    answerPayment: function (t, ok) {
      var o = state.orders[t];
      if (!o || o.status !== "pending") return;
      o.status = ok ? "paid" : "cancelled";
      if (ok) {
        o.paid_at = new Date().toISOString();
        var p = state.products[o.code];
        if (p && p.stock !== null) p.stock = Math.max(p.stock - o.qty, 0);
      }
      audit("update", "shop_orders", o.id, { status: "pending" }, { status: o.status, provider_state: ok ? "AUTHORIZED" : "ABORTED" });
      save();
    },
    fulfil: function (t) {
      var o = state.orders[t];
      if (!o || o.status !== "paid" || o.fulfilled_at) return;
      o.fulfilled_at = new Date().toISOString();
      audit("update", "shop_orders", o.id, { fulfilled_at: null }, { fulfilled_at: o.fulfilled_at }, "admin");
      save();
    },
    reset: function () { state = seed(); save(); },

    // Formatting shared by all pages
    kr: function (ore) {
      return (ore / 100).toLocaleString("da-DK", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " kr.";
    },
    esc: function (s) {
      return String(s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; });
    },
    when: function (iso) {
      if (!iso) return "—";
      return new Date(iso).toLocaleString("da-DK", { dateStyle: "medium", timeStyle: "short" });
    },
  };
})();
