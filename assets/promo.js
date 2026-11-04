// Promo for happymates.ai/shop on the presentation page. Appears when the visitor has scrolled half
// way down, or after 10 seconds, whichever comes first. Once per browser session; closing it keeps it closed.
//
// "Referral intact": the link carries the visitor's own campaign parameters (ref, utm_*, click ids)
// from this page's address, adds ref/utm_source for this site only when they are missing, and sends
// the full page address as the Referer (referrerpolicy, no rel=noreferrer).
(function () {
  "use strict";
  var TARGET = "https://happymates.ai/shop";
  var SOURCE = "gaer-self-service-shop";
  var DELAY_MS = 10000;
  var SCROLL_SHARE = 0.5;
  var SEEN = "gaer.promo.seen";
  var PASS_ON = /^(ref|via|aff|affiliate|partner|utm_[a-z_]+|gclid|gbraid|wbraid|fbclid|msclkid|li_fat_id|ttclid)$/i;

  function seen() { try { return sessionStorage.getItem(SEEN) === "1"; } catch (e) { return false; } }
  function markSeen() { try { sessionStorage.setItem(SEEN, "1"); } catch (e) { /* private mode */ } }

  function promoUrl() {
    var url = new URL(TARGET);
    new URLSearchParams(location.search).forEach(function (value, key) {
      if (PASS_ON.test(key) && value) url.searchParams.set(key, value);
    });
    if (!url.searchParams.has("ref")) url.searchParams.set("ref", SOURCE);
    if (!url.searchParams.has("utm_source")) {
      url.searchParams.set("utm_source", SOURCE);
      url.searchParams.set("utm_medium", "promo");
      url.searchParams.set("utm_campaign", "shop");
    }
    return url.href;
  }

  var box = null, timer = null;

  function close() {
    if (!box) return;
    box.classList.remove("show");
    var b = box;
    setTimeout(function () { b.remove(); }, 250);
    box = null;
    document.removeEventListener("keydown", onKey);
  }
  function onKey(ev) { if (ev.key === "Escape") close(); }

  function show() {
    if (box || seen()) return;
    cleanup();
    markSeen();
    box = document.createElement("aside");
    box.className = "promo";
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-labelledby", "promo-title");
    box.innerHTML =
      '<button class="promo-close" type="button" aria-label="Luk">×</button>' +
      '<div class="promo-eyebrow">Happy Mates</div>' +
      '<h2 id="promo-title">Vil du have din egen selvbetjente butik?</h2>' +
      "<p>Vi sætter butikken op for dig: varer, QR-etiketter og MobilePay på din egen server.</p>" +
      '<div class="promo-actions"><a class="btn mp promo-cta" referrerpolicy="no-referrer-when-downgrade">Se mere på happymates.ai/shop</a>' +
      '<button class="btn outline promo-no" type="button">Nej tak</button></div>';
    var cta = box.querySelector(".promo-cta");
    cta.href = promoUrl();
    box.querySelector(".promo-close").addEventListener("click", close);
    box.querySelector(".promo-no").addEventListener("click", close);
    cta.addEventListener("click", function () { cta.href = promoUrl(); });
    document.body.appendChild(box);
    document.addEventListener("keydown", onKey);
    requestAnimationFrame(function () { requestAnimationFrame(function () { if (box) box.classList.add("show"); }); });
  }

  function scrolledShare() {
    var d = document.documentElement;
    var room = d.scrollHeight - innerHeight;
    return room <= 0 ? 1 : scrollY / room;
  }
  function onScroll() { if (scrolledShare() >= SCROLL_SHARE) show(); }
  function cleanup() {
    clearTimeout(timer);
    removeEventListener("scroll", onScroll);
  }

  if (seen()) return;
  timer = setTimeout(show, DELAY_MS);
  addEventListener("scroll", onScroll, { passive: true });
  window.GAER_PROMO = { show: show, url: promoUrl };  // for testing in the console
})();
