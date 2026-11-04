// /w/butik — product page: keeps the total in step with the quantity and starts the MobilePay payment.
(function () {
  function kr(ore) {
    return (ore / 100).toLocaleString("da-DK", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " kr.";
  }
  var form = document.querySelector("form[data-checkout]");
  if (!form) return;
  var price = Number(form.getAttribute("data-price"));
  var qty = form.querySelector("select[name=quantity]");
  var total = form.querySelector("[data-total]");
  var error = form.querySelector("[data-error]");
  var button = form.querySelector("button[type=submit]");
  qty.addEventListener("change", function () { total.textContent = kr(price * Number(qty.value)); });
  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    error.hidden = true;
    button.disabled = true;
    button.textContent = "Åbner MobilePay …";
    fetch("/api/public/shop/checkout", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Gaer": "1" },
      body: JSON.stringify({ code: form.getAttribute("data-code"), quantity: Number(qty.value) }),
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (x) {
        if (!x.ok) throw new Error(typeof x.d.detail === "string" ? x.d.detail : "Noget gik galt. Prøv igen.");
        location.href = x.d.redirect_url;
      })
      .catch(function (e) {
        error.textContent = e.message || "Noget gik galt. Prøv igen.";
        error.hidden = false;
        button.disabled = false;
        button.textContent = "Betal med MobilePay";
      });
  });
})();
