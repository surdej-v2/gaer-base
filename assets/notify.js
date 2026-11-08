// Notification bar on index.html linking to the simulated backend. Closing it hides it for 30 days.
(function () {
  "use strict";
  var KEY = "gaer.notify.backend.closed";
  var bar = document.getElementById("notify");
  if (!bar) return;
  try {
    var closed = Number(localStorage.getItem(KEY));
    if (closed && Date.now() - closed < 30 * 864e5) { bar.remove(); return; }
  } catch (e) { /* storage blocked: show it */ }
  bar.querySelector(".notify-close").addEventListener("click", function () {
    try { localStorage.setItem(KEY, String(Date.now())); } catch (e) { /* ignore */ }
    bar.remove();
  });
})();
