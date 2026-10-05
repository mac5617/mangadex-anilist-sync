// Rank: keyboard shortcuts. 1/2/3 pick how it felt, ←/→ pick which you liked more, T too close, S skip, Enter next.
(function () {
  const stage = document.getElementById("rank-stage");
  if (!stage) return;
  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey || e.target.closest("input, textarea, select")) return;
    const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
    const button = stage.querySelector(`[data-key="${CSS.escape(key)}"]`);
    if (button && !button.disabled) {
      e.preventDefault();
      button.click();
    }
  });
  // Each new step: put focus where the next answer goes, so Enter/Space work too.
  stage.addEventListener("htmx:afterSwap", () => {
    (stage.querySelector("[autofocus]") || stage.querySelector(".rank-choice, .tier"))?.focus({ preventScroll: true });
  });
})();
