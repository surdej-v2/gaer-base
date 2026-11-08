// Draws QR labels for the demo products (from assets/store.js). URLs are built from where this page
// is served, so the codes work on any address (GitHub Pages, own domain, locally).
// <div data-labels> gets every product for sale; etiketter.html?kode=<code> prints just that one.
(function () {
  "use strict";
  function demoUrl(code) {
    return new URL("demo.html#/vare/" + code, location.href).href;
  }
  function svgFor(url) {
    var q = qrcode(0, "M");
    q.addData(url);
    q.make();
    var holder = document.createElement("div");
    holder.innerHTML = q.createSvgTag({ cellSize: 4, margin: 2 });
    var svg = holder.firstElementChild;
    var w = svg.getAttribute("width"), h = svg.getAttribute("height");
    if (!svg.getAttribute("viewBox")) svg.setAttribute("viewBox", "0 0 " + parseFloat(w) + " " + parseFloat(h));
    svg.removeAttribute("width");
    svg.removeAttribute("height");
    svg.setAttribute("role", "img");
    return svg;
  }
  function label(p) {
    var url = demoUrl(p.code);
    var el = document.createElement("a");
    el.className = "shop-label";
    el.href = url;
    el.innerHTML = '<div class="shop-label-name"></div><div class="shop-label-price"></div>' +
      '<div class="shop-label-qr"></div><div class="shop-label-cta">Scan og betal med MobilePay</div>' +
      '<div class="shop-label-url"></div>';
    el.querySelector(".shop-label-name").textContent = p.name;
    el.querySelector(".shop-label-price").textContent = window.GaerStore.kr(p.price);
    el.querySelector(".shop-label-qr").appendChild(svgFor(url));
    el.querySelector(".shop-label-url").textContent = url.replace(/^https?:\/\//, "");
    return el;
  }
  function fill() {
    var only = new URLSearchParams(location.search).get("kode");
    var products = window.GaerStore.products().filter(function (p) { return p.active && (!only || p.code === only); });
    document.querySelectorAll("[data-labels]").forEach(function (box) {
      box.innerHTML = "";
      products.forEach(function (p) { box.appendChild(label(p)); });
      if (!products.length) box.innerHTML = '<p class="muted">Ingen varer til salg.</p>';
    });
  }
  window.GaerLabels = { svgFor: svgFor, demoUrl: demoUrl };
  document.addEventListener("DOMContentLoaded", function () {
    fill();
    window.GaerStore.subscribe(fill);
  });
})();
