// Field drafting: the browser drives a draft one model call per request, up to WORKERS at once,
// and shows the server's view of it. Nothing here decides what is drafted or what it costs; every
// state shown is read from the drafts API.
"use strict";

(() => {
  const root = document.querySelector("[data-analyzing]");
  if (!root) return;
  const $ = (selector) => root.querySelector(selector);
  const id = root.dataset.template;
  let revision = Number(root.dataset.revision); // the template's current revision
  const base = `/api/templates/${id}/drafts`;
  const WORKERS = 4;
  const POLL_MS = 5000;

  let view = null; // the draft as the server last showed it
  let result = null; // its fields, once it is no longer running
  let driving = false;
  let inFlight = 0;
  let busy = ""; // "starting" or "applying" while that request is out
  let loaded = false;

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const usd = (value) => `$${Number(value).toFixed(value < 1 ? 4 : 2)}`;
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

  // ---- what the draft shows ----

  function counts() {
    const pages = view ? view.pages : [];
    const tally = (states) => pages.filter((p) => states.includes(p.state)).length;
    return {
      total: pages.length,
      proposed: tally(["drafted", "checking", "done", "lost"]),
      checked: tally(["done", "lost"]),
      lost: tally(["lost"]),
      available: tally(["pending", "drafted"]),
    };
  }

  function stageStates() {
    const c = counts();
    const pages = root.dataset.pages ? plural(Number(root.dataset.pages), "page") : "";
    const stages = [
      { state: "done", detail: `SHA-256 ${root.dataset.sha}${pages ? ` · ${pages}` : ""}` },
      { state: "pending", detail: "" },
      { state: "pending", detail: "" },
      { state: "pending", detail: "" },
      { state: "pending", detail: "Opens the record; the editor follows in the next slice" },
    ];
    if (busy === "starting") stages[1] = { state: "current", detail: "Reading every page of the source" };
    if (!view) return stages;
    const flags = view.flags.length ? ` · ${plural(view.flags.length, "flag")}` : "";
    stages[1] = { state: "done", detail: `${plural(c.total, "page")} · inventory ${view.inventory}${flags}` };
    const stopped = view.state === "STOPPED";
    const routeCheck = view.preflight === "PASSED" ? "" : `route check ${view.preflight.toLowerCase()} · `;
    stages[2] = {
      state: c.proposed === c.total && view.preflight === "PASSED" ? "done" : stopped ? "failed" : "current",
      detail: `${routeCheck}${c.proposed} of ${plural(c.total, "page")} finished`,
    };
    if (stages[2].state === "done" || c.checked > 0) {
      stages[3] = {
        state: c.checked === c.total ? "done" : stopped ? "failed" : "current",
        detail: `${c.checked} of ${plural(c.total, "page")} finished${c.lost ? ` · ${c.lost} with no answer` : ""}`,
      };
    }
    if (view.state === "APPLIED") {
      stages[4] = { state: "done", detail: `Saved as revision ${view.applied.revision}` };
    } else if (view.state !== "RUNNING") {
      stages[4] = { state: "current", detail: result ? `${plural(result.fields.length, "field")} ready to save` : "Reading the result" };
    }
    if (stopped && stages[3].state === "pending") stages[3].state = "failed";
    return stages;
  }

  const STATE_TEXT = { pending: "Pending", current: "In progress", done: "Done", failed: "Stopped" };

  function renderStages() {
    const states = stageStates();
    root.querySelectorAll("[data-stage]").forEach((item, index) => {
      const { state, detail } = states[index];
      item.dataset.state = state;
      item.setAttribute("aria-current", state === "current" ? "step" : "false");
      item.querySelector("[data-mark]").textContent = state === "done" ? "✓" : state === "failed" ? "!" : String(index + 1);
      item.querySelector("[data-state-text]").textContent = STATE_TEXT[state];
      item.querySelector("[data-detail]").textContent = detail;
    });
    const c = counts();
    const share = c.total ? Math.round((100 * (c.proposed + c.checked)) / (2 * c.total)) : 0;
    $("[data-progress]").setAttribute("aria-valuenow", String(share));
    $("[data-progress-bar]").style.width = `${share}%`;
  }

  function stopExplanation(reason) {
    if (reason.startsWith("budget")) {
      return ["Stopped at the spend cap", `Nothing more was sent: the next call could have taken drafting spend past the $${root.dataset.cap} cap. ${reason}`];
    }
    if (reason.startsWith("route violation")) {
      return ["Stopped: unexpected model route", `An answer came from a route other than the approved one, so nothing more was sent. ${reason}`];
    }
    if (reason.startsWith("preflight")) {
      return ["Stopped: the route check did not pass", `No page was sent. ${reason}`];
    }
    return ["Drafting stopped", reason];
  }

  function renderResult() {
    const box = $("[data-result]");
    const showing = view && view.state !== "RUNNING" && result;
    box.hidden = !showing;
    if (!showing) return;
    const status = result.status === "COMPLETE" ? "Every page was drafted and checked." : "Partial: some pages have no checked fields, so review every page.";
    const rejected = result.rejected.length ? ` ${plural(result.rejected.length, "proposed field")} failed validation and ${result.rejected.length === 1 ? "is" : "are"} left out.` : "";
    $("[data-result-line]").textContent = `${plural(result.fields.length, "field")} checked. ${status}${rejected}`;
    const byReason = new Map(); // pages that stopped for the same reason share one line
    result.unfinished.forEach((item) => byReason.set(item.status, [...(byReason.get(item.status) || []), item.page]));
    const lines = [...byReason].map(([status, numbers]) => {
      const line = document.createElement("li");
      const shown = numbers.length > 8 ? `${numbers.slice(0, 8).join(", ")} and ${numbers.length - 8} more` : numbers.join(", ");
      line.textContent = `${numbers.length === 1 ? "Page" : "Pages"} ${shown}: ${status}`;
      return line;
    });
    const list = $("[data-unfinished]");
    list.replaceChildren(...lines);
    list.hidden = lines.length === 0;
    const replaces = $("[data-replaces]");
    replaces.hidden = !(view.base_fields > 0 && view.state !== "APPLIED");
    replaces.textContent = `Saving replaces the ${plural(view.base_fields, "field")} saved in revision ${view.base_revision}.`;
  }

  function render() {
    renderStages();
    const stopped = $("[data-stopped]");
    stopped.hidden = !(view && view.state === "STOPPED" && view.stop_reason);
    if (!stopped.hidden) {
      const [title, text] = stopExplanation(view.stop_reason);
      $("[data-stopped-title]").textContent = title;
      $("[data-stopped-text]").textContent = text;
    }
    renderResult();
    if (view) {
      $("[data-draft-usd]").textContent = usd(view.spend.draft_usd);
      $("[data-total-usd]").textContent = usd(view.spend.total_usd);
    }
    const current = view && view.base_revision === revision;
    const stale = view && !current && view.state !== "APPLIED";
    $("[data-start]").hidden = !loaded || Boolean(view) || driving || busy !== "";
    $("[data-continue]").hidden = !(current && view.state === "RUNNING" && !driving);
    const apply = $("[data-apply]");
    apply.hidden = !(current && ["DRAFTED", "STOPPED"].includes(view.state) && result && result.fields.length);
    apply.disabled = busy !== "";
    apply.textContent = result ? `Save ${plural(result.fields.length, "field")} as revision ${revision + 1}` : "Save fields";
    $("[data-restart]").hidden = !(view && !driving && busy === "" && (stale || view.state === "APPLIED" || (current && view.state === "STOPPED")));
    $("[data-open-record]").hidden = !(view && view.state === "APPLIED");
    if (stale) showError("This draft was started on an earlier revision", `The template is now at revision ${revision}, so this draft can no longer be saved. Start a new draft to draft the current revision.`);
  }

  function showError(title, text) {
    $("[data-error-title]").textContent = title;
    $("[data-error-text]").textContent = text;
    $("[data-error]").hidden = false;
  }

  function hideError() {
    $("[data-error]").hidden = true;
  }

  // ---- talking to the drafts API ----

  async function readResult() {
    if (!view || view.state === "RUNNING") return;
    const answer = await studio.api("GET", `${base}/${view.id}/result`);
    if (answer.ok) result = answer.data;
    else showError("The result could not be read", answer.reason);
  }

  const RANK = { pending: 0, drafting: 1, drafted: 2, checking: 3, done: 4, lost: 4 };
  const progress = (v) => v.pages.reduce((sum, p) => sum + RANK[p.state], v.preflight === "PASSED" ? 1 : 0);

  // Answers to concurrent requests can arrive out of order; an older running view never replaces
  // a newer one.
  async function show(next) {
    if (view && next.id === view.id && next.state === "RUNNING" && view.state === "RUNNING" && progress(next) < progress(view)) return;
    const moved = !view || next.id !== view.id || next.state !== view.state || progress(next) !== progress(view);
    view = next;
    // Steps still out when a draft stops finish afterwards, so the result is read again as they do.
    if (view.state !== "RUNNING" && (moved || !result)) await readResult();
    render();
  }

  function pause(answer) {
    driving = false;
    const title = answer.status === 503 ? "Drafting paused" : "Drafting could not continue";
    showError(title, `${answer.reason} Calls already sent are kept; continue when this is resolved.`);
    render();
  }

  async function step() {
    inFlight += 1;
    const answer = await studio.api("POST", `${base}/${view.id}/step`);
    inFlight -= 1;
    if (!answer.ok) {
      pause(answer);
      return false;
    }
    await show(answer.data);
    return true;
  }

  async function refresh() {
    await sleep(POLL_MS);
    const answer = await studio.api("GET", `${base}/${view.id}`);
    if (!answer.ok) {
      pause(answer);
      return false;
    }
    await show(answer.data);
    return true;
  }

  // The route check goes first and alone: no page is sent until it has passed.
  async function routeCheck() {
    while (driving && view.state === "RUNNING" && view.preflight !== "PASSED") {
      const sent = view.preflight === "PENDING" ? await step() : await refresh(); // RUNNING: another browser sent it
      if (!sent) return;
    }
  }

  async function worker() {
    while (driving && view.state === "RUNNING") {
      if (counts().available > 0) {
        if (!(await step())) return;
      } else if (inFlight > 0) {
        return; // the calls out now leave any further work to the workers that sent them
      } else if (!(await refresh())) {
        return; // another browser holds the remaining calls
      }
    }
  }

  async function drive() {
    if (driving || !view || view.state !== "RUNNING") return;
    driving = true;
    hideError();
    render();
    await routeCheck();
    await Promise.all(Array.from({ length: WORKERS }, worker));
    driving = false;
    render();
  }

  async function start() {
    busy = "starting";
    hideError();
    render();
    const answer = await studio.api("POST", base, { base_revision: revision });
    busy = "";
    if (!answer.ok) {
      showError("Drafting could not start", answer.reason);
      render();
      return;
    }
    result = null;
    await show(answer.data);
    await drive();
  }

  async function apply() {
    busy = "applying";
    render();
    const answer = await studio.api("POST", `${base}/${view.id}/apply`);
    busy = "";
    if (!answer.ok) {
      showError("The fields were not saved", answer.reason);
      render();
      return;
    }
    revision = answer.data.template.revision;
    studio.toast(`Saved as revision ${revision}.`);
    result = answer.data.result;
    await show(answer.data.draft);
  }

  // ---- wiring ----

  $("[data-start]").addEventListener("click", start);
  $("[data-continue]").addEventListener("click", drive);
  $("[data-apply]").addEventListener("click", apply);
  $("[data-restart]").addEventListener("click", () => {
    view = null;
    result = null;
    start();
  });
  window.addEventListener("beforeunload", (event) => {
    if (inFlight > 0 || busy !== "") event.preventDefault();
  });

  (async () => {
    render();
    const answer = await studio.api("GET", `${base}/latest`);
    loaded = true;
    if (!answer.ok) {
      showError("Drafting is not available", answer.reason);
      $("[data-start]").hidden = true;
      return;
    }
    const autostart = root.dataset.autostart === "yes";
    if (autostart) window.history.replaceState(null, "", window.location.pathname);
    if (answer.data.draft) {
      await show(answer.data.draft);
      if (autostart && view.base_revision === revision) await drive();
    } else if (autostart) {
      await start();
    } else {
      render();
    }
  })();
})();
