// Demo of the buyer flow in /w/butik, entirely in the browser: no server, no real payment.
// Same markup and CSS classes as server/app/routes/shop.py, so it looks like the real thing.
// Routes (hash): #/  ·  #/vare/<code>  ·  #/betal/<token>  ·  #/ordre/<token>
(function () {
  "use strict";

  var PRODUCTS = {
    aebler: { name: "Æbler, 1 kg", description: "Ingrid Marie fra haven. Tag en pose fra kurven.", price: 2500, stock: 12 },
    honning: { name: "Honning, 450 g", description: "Sommerhonning fra egne bistader.", price: 7500, stock: 4 },
    aeg: { name: "Friske æg, 10 stk", description: "", price: 3500, stock: null },
  };
  window.GAER_DEMO_PRODUCTS = PRODUCTS;

  // Demo state per browser: orders and stock. Resets with "Nulstil demo".
  var KEY = "gaer.shopdemo";
  function load() {
    try { return JSON.parse(localStorage.getItem(KEY)) || { seq: 0, orders: {}, sold: {} }; }
    catch (e) { return { seq: 0, orders: {}, sold: {} }; }
  }
  function save(s) { try { localStorage.setItem(KEY, JSON.stringify(s)); } catch (e) { /* private mode: state lives until reload */ } }
  var state = load();

  function stock(code) {
    var p = PRODUCTS[code];
    return p.stock === null ? null : Math.max(p.stock - (state.sold[code] || 0), 0);
  }
  function kr(ore) {
    return (ore / 100).toLocaleString("da-DK", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " kr.";
  }
  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; });
  }
  function token() {
    var b = new Uint8Array(16);
    crypto.getRandomValues(b);
    return Array.prototype.map.call(b, function (x) { return ("0" + x.toString(16)).slice(-2); }).join("");
  }

  var main = document.getElementById("main");
  function render(html, title) {
    main.innerHTML = html;
    document.title = title + " · Gær butik (demo)";
    window.scrollTo(0, 0);
  }

  function lines(o) {
    return '<dl class="shop-lines"><dt>Vare</dt><dd>' + esc(o.name) + " × " + o.qty + "</dd>" +
      "<dt>Beløb</dt><dd>" + kr(o.amount) + "</dd></dl>";
  }

  function index() {
    var items = Object.keys(PRODUCTS).map(function (code) {
      var p = PRODUCTS[code];
      return '<a class="shop-item" href="#/vare/' + code + '"><span>' + esc(p.name) + "</span><strong>" +
        (stock(code) === 0 ? "Udsolgt" : kr(p.price)) + "</strong></a>";
    }).join("");
    render("<h1>Butik</h1><div class=\"shop-list\">" + items + "</div>" +
      '<p class="shop-small">Det her er en demo. Ingen rigtige penge, ingen server. ' +
      '<a href="#/nulstil">Nulstil demo</a></p>', "Butik");
  }

  function product(code) {
    var p = PRODUCTS[code];
    if (!p) return render('<h1>Varen findes ikke</h1><p>Koden er ikke i brug længere. <a href="#/">Se alle varer</a>.</p>', "Varen findes ikke");
    var left = stock(code), form;
    if (left === 0) {
      form = '<p class="shop-pill">Udsolgt</p>';
    } else {
      var top = Math.min(left === null ? 10 : left, 10), opts = "";
      for (var n = 1; n <= top; n++) opts += '<option value="' + n + '">' + n + "</option>";
      form = '<form class="shop-buy" id="buy">' +
        '<label>Antal <select name="quantity">' + opts + "</select></label>" +
        '<p class="shop-total">I alt <strong id="total">' + kr(p.price) + "</strong></p>" +
        '<button class="shop-button mobilepay" type="submit">Betal med MobilePay</button>' +
        (left !== null && left <= 10 ? '<p class="shop-small">' + left + " på lager</p>" : "") + "</form>";
    }
    render('<div class="shop-product"><h1>' + esc(p.name) + "</h1>" +
      (p.description ? '<p class="shop-desc">' + esc(p.description) + "</p>" : "") +
      '<p class="shop-price">' + kr(p.price) + "</p>" + form + "</div>", p.name);
    var f = document.getElementById("buy");
    if (!f) return;
    var qty = f.querySelector("select");
    qty.addEventListener("change", function () { document.getElementById("total").textContent = kr(p.price * Number(qty.value)); });
    f.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var b = f.querySelector("button");
      b.disabled = true;
      b.textContent = "Åbner MobilePay …";
      var t = token(), q = Number(qty.value);
      state.orders[t] = { id: ++state.seq, code: code, name: p.name, qty: q, amount: p.price * q, status: "pending", paid_at: null };
      save(state);
      setTimeout(function () { location.hash = "#/betal/" + t; }, 500);
    });
  }

  function pay(t) {
    var o = state.orders[t];
    if (!o) return notFound();
    if (o.status !== "pending") { location.hash = "#/ordre/" + t; return; }
    render('<div class="shop-receipt pending"><p class="shop-pill">Her ville MobilePay åbne · ingen rigtige penge</p>' +
      "<h1>" + kr(o.amount) + "</h1><p>" + esc(o.name) + " × " + o.qty + "</p>" +
      '<div class="shop-actions"><button class="shop-button" data-answer="approve">Godkend</button>' +
      '<button class="shop-button secondary" data-answer="reject">Afvis</button></div></div>', "MobilePay (demo)");
    main.querySelectorAll("[data-answer]").forEach(function (b) {
      b.addEventListener("click", function () {
        var ok = b.getAttribute("data-answer") === "approve";
        o.status = ok ? "paid" : "cancelled";
        if (ok) {
          o.paid_at = new Date().toISOString();
          state.sold[o.code] = (state.sold[o.code] || 0) + o.qty;
        }
        save(state);
        location.hash = "#/ordre/" + t;
      });
    });
  }

  function receipt(t) {
    var o = state.orders[t];
    if (!o) return notFound();
    if (o.status === "paid") {
      var d = new Date(o.paid_at);
      var when = d.toLocaleDateString("da-DK", { day: "2-digit", month: "2-digit", year: "numeric" }).replace(/\//g, ".") +
        " kl. " + d.toLocaleTimeString("da-DK", { hour: "2-digit", minute: "2-digit" }).replace(".", ":");
      return render('<div class="shop-receipt paid"><div class="shop-mark">✓</div><h1>Betalt</h1>' +
        '<p class="shop-order">Ordre <strong>#' + o.id + "</strong></p>" + lines(o) +
        '<p class="shop-small">Betalt ' + when + ". Tag din vare. Vis denne side, hvis nogen spørger.</p>" +
        '<p class="shop-small"><a href="#/">Tilbage til butikken</a></p></div>', "Ordre #" + o.id + " · Betalt");
    }
    if (o.status === "cancelled") {
      return render('<div class="shop-receipt cancelled"><div class="shop-mark">✕</div><h1>Betalingen blev ikke gennemført</h1>' +
        lines(o) + '<p class="shop-small">Der er ikke trukket penge.</p>' +
        '<a class="shop-button secondary" href="#/vare/' + o.code + '">Prøv igen</a></div>', "Betaling afbrudt");
    }
    render('<div class="shop-receipt pending"><div class="shop-mark spin">◌</div><h1>Venter på betaling …</h1>' + lines(o) +
      '<p class="shop-small">Godkend betalingen i MobilePay.</p>' +
      '<a class="shop-button" href="#/betal/' + t + '">Til betaling</a></div>', "Venter på betaling");
  }

  function notFound() { render("<h1>Ordren findes ikke</h1><p><a href=\"#/\">Til butikken</a></p>", "Ordren findes ikke"); }

  function route() {
    var parts = location.hash.replace(/^#\/?/, "").split("/");
    if (parts[0] === "vare") return product(parts[1]);
    if (parts[0] === "betal") return pay(parts[1]);
    if (parts[0] === "ordre") return receipt(parts[1]);
    if (parts[0] === "nulstil") {
      state = { seq: 0, orders: {}, sold: {} };
      save(state);
      location.hash = "#/";
      return;
    }
    index();
  }

  if (main) {
    window.addEventListener("hashchange", route);
    route();
  }
})();
