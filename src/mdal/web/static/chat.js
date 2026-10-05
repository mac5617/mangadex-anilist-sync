// Ask: send on Enter, clear the box as soon as a question is sent (and put it back if it fails),
// show the question and a "thinking" timer at once, grow the box with its text, ↑ recalls the last question.
(function () {
  const form = document.getElementById("chat-form");
  const log = document.getElementById("chat-log");
  const thread = document.getElementById("chat-thread");
  if (!form || !log || !thread) return;
  const input = form.querySelector("textarea");
  const send = document.getElementById("chat-send");
  let sent = "";        // the question in flight, restored to the box if it fails
  let pending = [];     // placeholder elements shown while the model answers
  let timer = null;

  function fit() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 192) + "px";
  }
  function update() {
    send.disabled = !input.value.trim() || pending.length > 0;
    fit();
  }
  function toBottom(smooth) {
    log.scrollTo({ top: log.scrollHeight, behavior: smooth ? "smooth" : "auto" });
  }
  function bubble(className, text) {
    const div = document.createElement("div");
    div.className = className;
    const p = document.createElement("p");
    p.className = "chat-bubble";
    p.textContent = text;          // text only: never parsed as HTML
    div.appendChild(p);
    return div;
  }
  function thinking() {
    const div = document.createElement("div");
    div.className = "chat-msg assistant";
    div.innerHTML = '<p class="chat-thinking"><span class="dots" aria-hidden="true"><span></span><span></span><span></span></span>' +
      '<span class="label">Thinking…</span></p>';
    const label = div.querySelector(".label");
    const started = Date.now();
    timer = setInterval(() => {
      const s = Math.round((Date.now() - started) / 1000);
      label.textContent = s < 15 ? "Thinking…" : `Thinking… ${s}s (large local models can take a minute)`;
    }, 1000);
    return div;
  }
  function clearPending() {
    pending.forEach((el) => el.remove());
    pending = [];
    clearInterval(timer);
  }
  function lastQuestion() {
    const asked = thread.querySelectorAll(".chat-msg.user .chat-bubble");
    return asked.length ? asked[asked.length - 1].textContent.trim() : "";
  }

  input.addEventListener("input", update);
  window.addEventListener("resize", fit);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      if (!send.disabled) form.requestSubmit();
    } else if (e.key === "ArrowUp" && !input.value) {
      const last = lastQuestion();
      if (last) {
        e.preventDefault();
        input.value = last;
        update();
        input.setSelectionRange(last.length, last.length);
      }
    }
  });

  document.addEventListener("click", (e) => {
    const chip = e.target.closest("[data-ask]");
    if (chip) {
      input.value = chip.textContent.trim();
      update();
      form.requestSubmit();
      return;
    }
    const retry = e.target.closest("[data-retry]");
    if (retry) {
      const failed = retry.closest(".chat-failed");
      const question = failed.dataset.question || "";
      failed.previousElementSibling?.remove();   // the question that failed
      failed.remove();
      input.value = question;
      update();
      if (input.value.trim()) form.requestSubmit();
    }
  });

  // Values are collected before this event, so the box can be cleared right away.
  form.addEventListener("htmx:beforeRequest", () => {
    sent = input.value.trim();
    thread.querySelector(".chat-empty")?.remove();
    pending = [bubble("chat-msg user pending", sent), thinking()];
    pending.forEach((el) => thread.appendChild(el));
    input.value = "";
    update();
    toBottom(true);
  });

  form.addEventListener("htmx:afterRequest", (e) => {
    clearPending();
    const ok = e.detail.successful;
    if (!ok) {
      // Network failure or a server error: nothing was swapped in, so say so and keep the question.
      const failed = document.createElement("div");
      failed.className = "chat-msg assistant chat-failed";
      failed.innerHTML = '<p class="notice err" role="alert">Shiori didn\'t answer. Is it still running?</p>';
      thread.appendChild(bubble("chat-msg user", sent));
      thread.appendChild(failed);
    }
    if (!ok || thread.lastElementChild?.classList.contains("chat-failed")) {
      if (!input.value) input.value = sent;   // put the question back to edit or resend
    }
    update();
    const asked = thread.querySelectorAll(".chat-msg.user");
    asked[asked.length - 1]?.scrollIntoView({ block: "start", behavior: "smooth" });
    input.focus();
  });

  // Open at the latest message.
  toBottom(false);
  update();
})();
