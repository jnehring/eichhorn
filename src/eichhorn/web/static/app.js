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
    const box = document.querySelector("[data-progress]");
    const q = (name) => box && box.querySelector("[data-p-" + name + "]");
    const fmtSize = (n) => {
      const units = ["B", "KB", "MB", "GB", "TB"];
      let i = 0;
      while (n >= 1000 && i < units.length - 1) { n /= 1000; i++; }
      return (i ? n.toFixed(1) : n.toFixed(0)) + " " + units[i];
    };
    const fmtTime = (s) => {
      s = Math.round(s);
      if (s < 60) return s + " s";
      const m = Math.floor(s / 60);
      if (m < 60) return m + " min";
      return Math.floor(m / 60) + " h " + (m % 60) + " min";
    };
    const phaseText = (p) => {
      if (p.phase === "transfer") {
        let t = "copying " + p.detail;
        if (p.bytes_total) t += " · " + fmtSize(p.bytes_done) + " of " + fmtSize(p.bytes_total);
        if (p.speed) t += " · " + fmtSize(p.speed) + "/s";
        return t;
      }
      if (p.phase === "dump") return "database dump " + p.detail + " · " + fmtSize(p.bytes_done);
      const names = { start: "starting", hooks: "hooks (" + p.detail + ")",
        listing: "listing " + p.detail, finish: "checksums and index",
        copy: "copying to " + p.detail, prune: "pruning", done: "done" };
      return names[p.phase] || p.phase;
    };
    source.addEventListener("progress", (ev) => {
      if (!box) return;
      const p = JSON.parse(ev.data);
      box.hidden = false;
      q("target").textContent = p.target;
      q("detail").textContent = phaseText(p);
      q("pct-target").textContent = p.pct_target.toFixed(0) + " %";
      q("eta-target").textContent = p.eta_target ? "~" + fmtTime(p.eta_target) : "";
      q("bar-target").style.width = p.pct_target + "%";
      if (p.count > 1 && p.pct_all !== null) {
        q("all-block").hidden = false;
        q("count").textContent = "(" + p.index + " of " + p.count + ")";
        q("pct-all").textContent = p.pct_all.toFixed(0) + " %";
        q("eta-all").textContent = p.eta_all ? "~" + fmtTime(p.eta_all) + " left" : "";
        q("bar-all").style.width = p.pct_all + "%";
      }
    });
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
