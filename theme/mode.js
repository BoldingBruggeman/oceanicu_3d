/* Light/dark mode. Loaded in <head> (not deferred), so a reader's earlier choice applies before the
   page is drawn: it sets data-theme="light" or "dark" on <html>, which style.css and code.css follow;
   without a choice the system setting (prefers-color-scheme) decides. Adds the button after the last
   menu item of the top bar; the choice is kept per browser (localStorage). */
(function () {
  var KEY = "bb-theme", root = document.documentElement;
  try {
    var saved = localStorage.getItem(KEY);
    if (saved === "light" || saved === "dark") root.setAttribute("data-theme", saved);
  } catch (e) { /* storage blocked: follow the system setting */ }

  function current() {
    return root.getAttribute("data-theme") ||
      (window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  }

  function addButton() {
    var bar = document.querySelector(".topbar .topin");
    if (!bar || bar.querySelector(".bb-mode")) return;
    var b = document.createElement("button");
    b.type = "button";
    b.className = "bb-mode";
    function label() {
      var dark = current() === "dark";
      b.textContent = dark ? "☀" : "☾";          // sun: go light; moon: go dark
      b.title = dark ? "Light mode" : "Dark mode";
      b.setAttribute("aria-label", b.title);
    }
    b.addEventListener("click", function () {
      var next = current() === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem(KEY, next); } catch (e) { /* not kept */ }
      label();
    });
    label();
    (bar.querySelector("nav") || bar).appendChild(b);       // after the last menu item
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", addButton);
  else addButton();
})();
