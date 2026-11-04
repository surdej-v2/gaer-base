// Simulated /admin/shop for the Pages site: same screens as web/src/admin-app/pages/Shop.tsx,
// backed by assets/store.js instead of the Python API. Views (hash): #varer #ordrer #etiketter #audit
(function () {
  "use strict";
  var S = window.GaerStore, kr = S.kr, esc = S.esc, when = S.when;
  var view = document.getElementById("view");
  var editing = null;      // product code being edited, "new", or null
  var notice = "";
  var orderFilter = "";

  function parseKr(value) {
    var clean = String(value).replace(/\s|kr\.?/gi, "").replace(/\./g, "").replace(",", ".");
    if (!/^\d+(\.\d{1,2})?$/.test(clean)) return null;
    var ore = Math.round(Number(clean) * 100);
    return ore > 0 ? ore : null;
  }

  function current() {
    var v = location.hash.replace("#", "");
    return ["varer", "ordrer", "etiketter", "audit"].indexOf(v) >= 0 ? v : "varer";
  }

  function header(path, title, text, actions) {
    return '<div class="adm-head"><div><div class="eyebrow">/admin/' + path + "</div><h1 class=\"adm-h1\">" + title + "</h1>" +
      (text ? '<p class="adm-lead">' + text + "</p>" : "") + "</div>" + (actions || "") + "</div>";
  }

  function sold(code) {
    var n = 0, sum = 0;
    S.orders().forEach(function (o) { if (o.code === code && o.status === "paid") { n += o.qty; sum += o.amount; } });
    return [n, sum];
  }

  // ── Varer ───────────────────────────────────────────────────────────────
  function products() {
    var list = S.products();
    var form = "";
    if (editing) {
      var p = editing === "new" ? { name: "", description: "", price: null, stock: null, active: true } : S.product(editing);
      form = '<form class="adm-card adm-form" id="pform">' +
        '<div class="eyebrow">' + (editing === "new" ? "Ny vare" : "Redigér vare") + "</div>" +
        '<label>Navn<input name="name" required value="' + esc(p.name) + '"></label>' +
        '<label>Beskrivelse (valgfri)<textarea name="description">' + esc(p.description) + "</textarea></label>" +
        '<div class="adm-2"><label>Pris (kr.)<input name="price" inputmode="decimal" placeholder="25,00" value="' +
        (p.price ? (p.price / 100).toFixed(2).replace(".", ",") : "") + '"></label>' +
        '<label>Lager<input name="stock" inputmode="numeric" placeholder="ubegrænset" value="' + (p.stock === null ? "" : p.stock) + '"></label></div>' +
        '<label class="adm-check"><input type="checkbox" name="active"' + (p.active ? " checked" : "") + "> Til salg</label>" +
        '<p class="adm-error" id="perr" hidden></p>' +
        '<div class="adm-row"><button class="btn" type="submit">Gem</button>' +
        '<button class="btn outline" type="button" id="cancel">Annullér</button>' +
        (editing !== "new" ? '<button class="btn danger" type="button" id="del">Slet</button>' : "") + "</div></form>";
    }
    var rows = list.map(function (p) {
      var s = sold(p.code);
      var status = !p.active ? '<span class="pill">Skjult</span>' : p.stock === 0 ? '<span class="pill red">Udsolgt</span>' : '<span class="pill green">Til salg</span>';
      return '<tr class="click" data-code="' + p.code + '"><td><strong>' + esc(p.name) + '</strong><div class="adm-mono">/w/butik/' + p.code + "</div></td>" +
        '<td class="num">' + kr(p.price) + '</td><td class="num">' + (p.stock === null ? "∞" : p.stock) + '</td><td class="num">' + s[0] +
        '</td><td class="num">' + kr(s[1]) + "</td><td>" + status + "</td>" +
        '<td class="adm-nowrap"><a class="btn outline adm-sm" href="etiketter.html?kode=' + p.code + '" target="_blank" rel="noopener">Udskriv</a> ' +
        '<a class="adm-link" href="demo.html#/vare/' + p.code + '" target="_blank" rel="noopener">Åbn</a></td></tr>';
    }).join("");
    view.innerHTML = header("shop", "Butik", "Hver vare har en QR-kode. Kunden scanner, vælger antal og betaler med MobilePay.") +
      '<div class="adm-row"><button class="btn mp" id="new">Ny vare</button>' +
      '<a class="btn outline" href="etiketter.html" target="_blank" rel="noopener">Udskriv alle QR-koder</a></div>' +
      (notice ? '<div class="adm-ok">' + esc(notice) + "</div>" : "") + form +
      (list.length ? '<div class="adm-table"><table><thead><tr><th>Vare</th><th>Pris</th><th>Lager</th><th>Solgt</th><th>Omsætning</th><th>Status</th><th>QR</th></tr></thead><tbody>' +
        rows + "</tbody></table></div>" : '<p class="adm-muted">Ingen varer endnu. Opret den første.</p>');

    view.querySelector("#new").onclick = function () { editing = "new"; notice = ""; render(); };
    view.querySelectorAll("tr.click").forEach(function (tr) {
      tr.addEventListener("click", function (ev) {
        if (ev.target.closest("a")) return;
        editing = tr.getAttribute("data-code"); notice = ""; render();
        view.querySelector("#pform").scrollIntoView({ behavior: "smooth", block: "nearest" });
      });
    });
    var f = view.querySelector("#pform");
    if (!f) return;
    f.querySelector("input[name=name]").focus();
    view.querySelector("#cancel").onclick = function () { editing = null; render(); };
    var del = view.querySelector("#del");
    if (del) del.onclick = function () {
      var p = S.product(editing);
      if (!confirm("Slet " + p.name + "? QR-koden holder op med at virke. Vil du hellere skjule varen, så fjern fluebenet ved \"Til salg\".")) return;
      S.deleteProduct(editing); notice = p.name + " er slettet"; editing = null; render();
    };
    f.onsubmit = function (ev) {
      ev.preventDefault();
      var err = view.querySelector("#perr");
      var el = f.elements;
      var name = el["name"].value.trim(), price = parseKr(el["price"].value), stock = el["stock"].value.trim();
      var msg = !name ? "Skriv et navn" : price === null ? "Skriv en pris i kroner, fx 25 eller 12,50"
        : stock !== "" && !/^\d+$/.test(stock) ? "Lager skal være et helt tal eller tomt" : "";
      if (msg) { err.textContent = msg; err.hidden = false; return; }
      var data = { name: name, description: el["description"].value, price: price, stock: stock === "" ? null : Number(stock), active: el["active"].checked };
      if (editing === "new") S.addProduct(data); else S.updateProduct(editing, data);
      notice = name + " er gemt"; editing = null; render();
    };
  }

  // ── Ordrer ──────────────────────────────────────────────────────────────
  var STATUS = { pending: ["Afventer", "amber"], paid: ["Betalt", "green"], cancelled: ["Afbrudt", "red"] };
  function orders() {
    var all = S.orders();
    var today = new Date(); today.setHours(0, 0, 0, 0);
    var month = Date.now() - 30 * 864e5;
    var t = 0, tn = 0, m = 0, open = 0;
    all.forEach(function (o) {
      if (o.status !== "paid") return;
      var paid = new Date(o.paid_at).getTime();
      if (paid >= today.getTime()) { t += o.amount; tn++; }
      if (paid >= month) m += o.amount;
      if (!o.fulfilled_at) open++;
    });
    var shown = all.filter(function (o) {
      return !orderFilter || (orderFilter === "unfulfilled" ? o.status === "paid" && !o.fulfilled_at : o.status === orderFilter);
    });
    var rows = shown.map(function (o) {
      var s = STATUS[o.status];
      var act = o.status === "paid" ? (o.fulfilled_at ? '<span class="adm-muted">' + when(o.fulfilled_at) + "</span>"
        : '<button class="btn outline adm-sm" data-fulfil="' + o.token + '">Udlevér</button>')
        : o.status === "pending" ? '<a class="adm-link" href="demo.html#/betal/' + o.token + '" target="_blank" rel="noopener">Til betaling</a>' : "";
      return '<tr><td class="num"><strong>#' + o.id + "</strong></td><td>" + when(o.paid_at || o.created_at) + "</td><td>" + esc(o.name) +
        '</td><td class="num">' + o.qty + '</td><td class="num">' + kr(o.amount) + '</td><td><span class="pill ' + s[1] + '">' + s[0] + "</span></td><td>" + act + "</td></tr>";
    }).join("");
    view.innerHTML = header("shop", "Ordrer", "Ordrer fra demo-butikken. Ingen persondata: kun vare, antal og beløb.") +
      '<div class="adm-stats"><div class="adm-card"><div class="eyebrow">I dag</div><div class="adm-stat">' + kr(t) + '</div><div class="adm-muted">' + tn + ' ordrer</div></div>' +
      '<div class="adm-card"><div class="eyebrow">Seneste 30 dage</div><div class="adm-stat">' + kr(m) + "</div></div>" +
      '<div class="adm-card"><div class="eyebrow">Mangler udlevering</div><div class="adm-stat">' + open + "</div></div></div>" +
      '<label class="adm-filter">Vis <select id="filter">' +
      [["", "Alle"], ["unfulfilled", "Betalt, ikke udleveret"], ["paid", "Betalt"], ["pending", "Afventer betaling"], ["cancelled", "Afbrudt"]]
        .map(function (o) { return '<option value="' + o[0] + '"' + (o[0] === orderFilter ? " selected" : "") + ">" + o[1] + "</option>"; }).join("") +
      "</select></label>" +
      '<div class="adm-table"><table><thead><tr><th>Ordre</th><th>Tid</th><th>Vare</th><th>Antal</th><th>Beløb</th><th>Status</th><th>Udlevering</th></tr></thead><tbody>' +
      (rows || '<tr><td colspan="7" class="adm-muted">Ingen ordrer endnu. <a href="demo.html" target="_blank" rel="noopener">Køb noget i demo-butikken</a>.</td></tr>') +
      "</tbody></table></div>";
    view.querySelector("#filter").onchange = function (ev) { orderFilter = ev.target.value; render(); };
    view.querySelectorAll("[data-fulfil]").forEach(function (b) { b.onclick = function () { S.fulfil(b.getAttribute("data-fulfil")); }; });
  }

  // ── QR-etiketter ────────────────────────────────────────────────────────
  function labels() {
    view.innerHTML = header("shop/print", "QR-etiketter", "Etiketterne til at sætte ved varerne. Scan en med telefonen, så åbner varen i demo-butikken.",
      '<a class="btn" href="etiketter.html" target="_blank" rel="noopener">Åbn til print</a>') +
      '<div class="shop-labels" data-labels></div>';
    var box = view.querySelector("[data-labels]");
    var list = S.products().filter(function (p) { return p.active; });
    if (!list.length) { box.innerHTML = '<p class="adm-muted">Ingen varer til salg.</p>'; return; }
    list.forEach(function (p) {
      var url = window.GaerLabels.demoUrl(p.code);
      var el = document.createElement("a");
      el.className = "shop-label"; el.href = url; el.target = "_blank"; el.rel = "noopener";
      el.innerHTML = '<div class="shop-label-name">' + esc(p.name) + '</div><div class="shop-label-price">' + kr(p.price) +
        '</div><div class="shop-label-qr"></div><div class="shop-label-cta">Scan og betal med MobilePay</div>';
      el.querySelector(".shop-label-qr").appendChild(window.GaerLabels.svgFor(url));
      box.appendChild(el);
    });
  }

  // ── Audit-log ───────────────────────────────────────────────────────────
  function auditLog() {
    var rows = S.auditLog().map(function (a) {
      var change = a.after ? Object.keys(a.after).map(function (k) {
        var v = a.after[k];
        return esc(k) + ": " + esc(a.before && k in a.before ? JSON.stringify(a.before[k]) + " → " : "") + esc(JSON.stringify(v));
      }).join("<br>") : a.before ? "slettet: " + esc(JSON.stringify(a.before)) : "";
      return "<tr><td class=\"adm-nowrap\">" + when(a.ts) + '</td><td><span class="pill">' + esc(a.action) + "</span></td><td class=\"adm-mono\">" +
        esc(a.table) + " #" + esc(a.row_id) + "</td><td>" + (a.actor === "admin" ? "Admin" : '<span class="adm-muted">Køber / system</span>') +
        '</td><td class="adm-mono adm-wrapcell">' + change + "</td></tr>";
    }).join("");
    view.innerHTML = header("logs/audit", "Audit-log", "Hver ændring af varer og ordrer bliver logget. I den rigtige backend kan loggen ikke ændres eller slettes af appen.") +
      '<div class="adm-table"><table><thead><tr><th>Tid</th><th>Handling</th><th>Række</th><th>Hvem</th><th>Ændring</th></tr></thead><tbody>' +
      (rows || '<tr><td colspan="5" class="adm-muted">Intet logget endnu.</td></tr>') + "</tbody></table></div>";
  }

  function render() {
    var v = current();
    document.querySelectorAll("[data-view]").forEach(function (a) { a.classList.toggle("active", a.getAttribute("data-view") === v); });
    var open = S.orders().filter(function (o) { return o.status === "paid" && !o.fulfilled_at; }).length;
    var badge = document.getElementById("badge");
    badge.hidden = !open; badge.textContent = open;
    if (v === "ordrer") orders(); else if (v === "etiketter") labels(); else if (v === "audit") auditLog(); else products();
  }

  document.getElementById("reset").onclick = function () {
    if (!confirm("Nulstil demoen? Varer, ordrer og log går tilbage til start.")) return;
    S.reset(); editing = null; notice = "";
  };
  window.addEventListener("hashchange", function () { editing = null; notice = ""; render(); });
  S.subscribe(function () { if (!editing) render(); });  // a purchase in another tab shows up right away
  render();
})();
