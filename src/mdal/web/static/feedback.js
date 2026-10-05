// Every button answers a press: it presses in (CSS :active), shows it's working while its request runs (and
// can't be pressed twice), and shows a tick for a moment once a quick save is done.
(function () {
  function buttonFor(detail) {
    const event = detail.requestConfig && detail.requestConfig.triggeringEvent;
    if (event && event.submitter) return event.submitter;                 // a form, sent by one of its buttons
    const elt = detail.elt;
    if (!elt || !elt.closest) return null;
    const own = elt.closest("button, .button");
    if (own) return own;
    return elt.tagName === "FORM" ? elt.querySelector("button:not([type=button])") : null;
  }

  document.addEventListener("htmx:beforeRequest", (e) => {
    const b = buttonFor(e.detail);
    if (b) { b.classList.remove("is-done"); b.classList.add("is-busy"); b.setAttribute("aria-busy", "true"); }
  });

  document.addEventListener("htmx:afterRequest", (e) => {
    const b = buttonFor(e.detail);
    if (!b) return;
    b.classList.remove("is-busy");
    b.removeAttribute("aria-busy");
    if (e.detail.successful && b.isConnected) {            // still on the page (not swapped away): show the tick
      b.classList.add("is-done");
      setTimeout(() => b.classList.remove("is-done"), 1400);
    }
  });

  // Ordinary forms (Settings, restore...) load a new page: the button shows it's working until then.
  document.addEventListener("submit", (e) => {
    if (e.defaultPrevented || e.target.matches("[hx-post], [hx-get]")) return;
    const b = e.submitter || e.target.querySelector("button:not([type=button])");
    if (b) { b.classList.add("is-busy"); b.setAttribute("aria-busy", "true"); }
  });
  // Coming back with the browser's Back button restores the page as it was: clear any working state.
  window.addEventListener("pageshow", () => {
    document.querySelectorAll(".is-busy").forEach((b) => { b.classList.remove("is-busy"); b.removeAttribute("aria-busy"); });
  });
})();
