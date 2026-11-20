// Eichhorn web UI: live job logs, confirmations and small navigation helpers.
"use strict";

document.addEventListener("DOMContentLoaded", () => {
  // Live log of a background job (Server-Sent Events).
  const log = document.querySelector("[data-events]");
  if (log) {
    const state = document.querySelector("[data-job-state]");
    const source = new EventSource(log.dataset.events);
    source.onmessage = (ev) => {
      const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 20;
      log.textContent += ev.data + "\n";
      if (atBottom) log.scrollTop = log.scrollHeight;
    };
    source.addEventListener("done", (ev) => {
      source.close();
      if (state) {
        state.textContent = ev.data;
        state.className = "badge " + ev.data;
      }
      const after = document.querySelector("[data-after-job]");
      if (after) after.hidden = false;
    });
  }

  // Ask before forms marked with data-confirm are sent.
  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (ev) => {
      if (!window.confirm(form.dataset.confirm)) ev.preventDefault();
    });
  });

  // Selects with data-nav jump to the chosen URL.
  document.querySelectorAll("select[data-nav]").forEach((sel) => {
    sel.addEventListener("change", () => { window.location.href = sel.value; });
  });
});
