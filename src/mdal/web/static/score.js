// Series page score slider: the badge, its colour and the score's word follow the slider as you drag; Save wakes
// up once the score differs from the saved one. Works on boxes swapped in later too (event delegation).
(function () {
  function tier(v) { return v >= 6.8 ? "liked" : v >= 4 ? "fine" : "disliked"; }

  function sync(slider) {
    const form = slider.closest(".score-box");
    if (!form) return;
    const value = Number(slider.value);
    const words = JSON.parse(form.dataset.scoreWords || "{}");
    const badge = form.querySelector("[data-score-badge]");
    const word = form.querySelector("[data-score-word]");
    const save = form.querySelector(".score-save");
    badge.textContent = Number.isInteger(value) ? String(value) : value.toFixed(1);
    badge.className = "score-badge tier-" + tier(value);
    word.textContent = words[String(Math.round(value))] || "";
    slider.classList.remove("tone-none", "tone-liked", "tone-fine", "tone-disliked");
    slider.classList.add("tone-" + tier(value));
    slider.style.setProperty("--fill", ((value - 1) / 9 * 100) + "%");
    if (save && !slider.disabled) save.disabled = form.dataset.saved !== "" && Number(form.dataset.saved) === value;
  }

  document.addEventListener("input", (e) => { if (e.target.matches(".score-slider")) sync(e.target); });
  function paint(root) {
    root.querySelectorAll(".score-slider").forEach((s) => {
      const form = s.closest(".score-box");
      if (!s.classList.contains("tone-none")) s.classList.add("tone-" + tier(Number(s.value)));  // unscored stays grey
      // Unscored: Save waits for the first move; scored: it waits for a change.
      if (form && form.dataset.saved === "") form.querySelector(".score-save").disabled = true;
    });
  }
  document.addEventListener("DOMContentLoaded", () => paint(document));
  document.addEventListener("htmx:afterSwap", (e) => paint(e.target.parentNode || document));
})();
