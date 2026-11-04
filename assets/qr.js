// Draws QR codes for the demo products. URLs are built from where this page is served,
// so the codes work on any GitHub Pages address (public or private) and locally.
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
  function kr(ore) {
    return (ore / 100).toLocaleString("da-DK", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " kr.";
  }
  function label(code, p) {
    var url = demoUrl(code);
    var el = document.createElement("a");
    el.className = "shop-label";
    el.href = url;
    el.innerHTML = '<div class="shop-label-name"></div><div class="shop-label-price"></div>' +
      '<div class="shop-label-qr"></div><div class="shop-label-cta">Scan og betal med MobilePay</div>' +
      '<div class="shop-label-url"></div>';
    el.querySelector(".shop-label-name").textContent = p.name;
    el.querySelector(".shop-label-price").textContent = kr(p.price);
    el.querySelector(".shop-label-qr").appendChild(svgFor(url));
    el.querySelector(".shop-label-url").textContent = url.replace(/^https?:\/\//, "");
    return el;
  }
  document.addEventListener("DOMContentLoaded", function () {
    var products = window.GAER_DEMO_PRODUCTS || {};
    document.querySelectorAll("[data-labels]").forEach(function (box) {
      Object.keys(products).forEach(function (code) { box.appendChild(label(code, products[code])); });
    });
  });
})();
