// Shared page behaviour: the account menu, signing out, the library's filters, the toast, and
// drafting that goes on in the background.
"use strict";

window.studio = (() => {
  let toastTimer = null;
  let refreshLibrary = null; // set on the library: shows the current view again in place

  function toast(message) {
    const box = document.querySelector("[data-toast]");
    if (!box) return;
    box.textContent = message;
    box.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { box.hidden = true; }, 5000);
  }

  // The reason in an API error answer: {"detail": "..."} or {"detail": {"errors": [...]}}.
  function reason(status, text) {
    let body = null;
    try { body = JSON.parse(text); } catch { /* not JSON */ }
    const detail = body && body.detail;
    if (status === 401) return "Your studio sign-in has expired. Reload the page to sign in again.";
    if (typeof detail === "string") return detail;
    if (detail && Array.isArray(detail.errors)) return detail.errors.join("; ");
    if (detail && typeof detail.reason === "string") return detail.reason;
    return `The studio answered ${status}.`;
  }

  async function api(method, path, body) {
    const options = { method, headers: {} };
    if (body !== undefined) {
      options.headers["content-type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    let response;
    try {
      response = await fetch(path, options);
    } catch {
      return { ok: false, status: 0, data: null, reason: "The studio could not be reached." };
    }
    const text = await response.text();
    let data = null;
    try { data = JSON.parse(text); } catch { /* not JSON */ }
    const failed = response.ok ? null : reason(response.status, text);
    return { ok: response.ok, status: response.status, data, reason: failed };
  }

  function setUpMenu() {
    const button = document.querySelector("[data-menu-button]");
    const menu = document.querySelector("[data-menu]");
    if (!button || !menu) return;
    const close = () => { menu.hidden = true; button.setAttribute("aria-expanded", "false"); };
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      const opening = menu.hidden;
      menu.hidden = !opening;
      button.setAttribute("aria-expanded", String(opening));
    });
    document.addEventListener("click", (event) => { if (!menu.contains(event.target)) close(); });
    document.addEventListener("keydown", (event) => { if (event.key === "Escape") close(); });
    const signOut = menu.querySelector("[data-sign-out]");
    signOut.addEventListener("click", async () => {
      const answer = await api("POST", "/auth/logout");
      if (answer.ok) {
        document.body.replaceChildren();
        const note = document.createElement("p");
        note.className = "page";
        note.textContent = "Signed out of the studio. You are still signed in to Vercel.";
        document.body.append(note);
      } else {
        toast(answer.reason);
      }
    });
  }

  function setUpFilters() {
    document.addEventListener("change", (event) => {
      if (event.target.matches("[data-autosubmit]")) event.target.form.requestSubmit();
    });
  }

  // The control to focus again once the library is swapped: the same search box, select, column,
  // or pager button, and for a tab, the tab now selected.
  function focusKey(node) {
    if (!node || !node.closest("[data-library]")) return null;
    if (node.matches(".tab")) return '.tab[aria-current="page"]';
    if (node.name) return `[name="${node.name}"]`;
    if (node.getAttribute("aria-label")) return `[aria-label="${node.getAttribute("aria-label")}"]`;
    const header = node.closest("th");
    if (header) return `th:nth-child(${header.cellIndex + 1}) a`;
    return null;
  }

  // The library swaps its results in place: a tab, sort, page or search fetches the same page and
  // replaces the main region, so the screen never goes blank between views. Anything unexpected
  // (an error, a sign-in redirect, a different page) falls back to a normal load.
  function setUpLibrary() {
    if (!document.querySelector("[data-library]")) return;
    let latest = 0;

    async function show(url, push) {
      const ticket = ++latest;
      const main = document.querySelector("[data-library]");
      const key = focusKey(document.activeElement);
      const busy = setTimeout(() => main.setAttribute("aria-busy", "true"), 150);
      let next = null;
      let title = document.title;
      try {
        const response = await fetch(url, { headers: { accept: "text/html" } });
        if (response.ok && !response.redirected) {
          const page = new DOMParser().parseFromString(await response.text(), "text/html");
          next = page.querySelector("[data-library]");
          title = page.title;
        }
      } catch { /* loaded normally below */ }
      clearTimeout(busy);
      if (ticket !== latest) return;
      if (!next) {
        window.location.assign(url);
        return;
      }
      if (push) window.history.pushState(null, "", url);
      main.replaceWith(document.adoptNode(next));
      document.title = title;
      const target = key && next.querySelector(key);
      if (target && target.getAttribute("aria-disabled") !== "true") target.focus();
    }

    document.addEventListener("click", (event) => {
      const link = event.target.closest("[data-library] a[href]");
      if (!link || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const url = new URL(link.href);
      if (url.origin !== window.location.origin || url.pathname !== window.location.pathname) return;
      event.preventDefault();
      if (link.matches(".tab")) {
        link.closest(".tabs").querySelectorAll(".tab").forEach((tab) => tab.removeAttribute("aria-current"));
        link.setAttribute("aria-current", "page");
      }
      show(url.href, true);
    });
    document.addEventListener("submit", (event) => {
      const form = event.target.closest("[data-library] form[data-filters]");
      if (!form) return;
      event.preventDefault();
      const url = new URL(form.action);
      url.search = new URLSearchParams(new FormData(form)).toString();
      show(url.href, true);
    });
    window.addEventListener("popstate", () => show(window.location.href, false));
    refreshLibrary = () => show(window.location.href, false);
  }

  // ---- drafting that goes on while the studio is open ----
  // The drafts this browser drives are kept here, so any studio page can carry one on after its
  // drafting page is left. A lock per draft keeps one tab driving it; the server's claims keep
  // every model call single regardless. The step that finishes a draft saves its fields.

  const DRAFTING_KEY = "studio.drafting";
  const WORKERS = 4;
  const IDLE_MS = 3000;
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

  function tracked() {
    try {
      const list = JSON.parse(window.localStorage.getItem(DRAFTING_KEY));
      return Array.isArray(list) ? list : [];
    } catch {
      return [];
    }
  }

  function keep(list) {
    try { window.localStorage.setItem(DRAFTING_KEY, JSON.stringify(list)); } catch { /* not kept */ }
  }

  const drafting = {
    track(entry) { keep([...tracked().filter((e) => e.draft !== entry.draft), entry]); },
    untrack(draftId) { keep(tracked().filter((e) => e.draft !== draftId)); },
    isTracked(draftId) { return tracked().some((e) => e.draft === draftId); },
    // Runs `work(held)` holding this draft's lock if no other tab holds it.
    hold(draftId, work) {
      if (!navigator.locks) return work(true);
      return navigator.locks.request(`studio-draft-${draftId}`, { ifAvailable: true }, (lock) => work(Boolean(lock)));
    },
  };

  const RANK = { pending: 0, drafting: 1, drafted: 2, checking: 3, done: 4, lost: 4 };
  const progress = (v) => v.pages.reduce((sum, p) => sum + RANK[p.state], v.preflight === "PASSED" ? 1 : 0);

  function finished(entry, view) {
    if (view.state === "APPLIED") {
      toast(`${entry.title}: drafting finished. ${plural(view.applied.fields, "field")} saved; it needs review.`);
    } else if (view.state === "DRAFTED") {
      toast(`${entry.title}: drafting finished, but nothing was saved: ${view.not_applied || "open it to see why"}.`);
    } else {
      toast(`${entry.title}: drafting stopped. Open it to see why.`);
    }
    if (refreshLibrary) refreshLibrary();
  }

  async function carryOn(entry) {
    const path = `/api/templates/${entry.template}/drafts/${entry.draft}/step`;
    let view = null;
    let paused = false;
    const send = async () => {
      const answer = await api("POST", path);
      if (!answer.ok) {
        paused = true;
        if ([404, 409, 422].includes(answer.status)) drafting.untrack(entry.draft); // it cannot go on
        return;
      }
      const next = answer.data;
      if (!view || (view.state === "RUNNING" && (next.state !== "RUNNING" || progress(next) >= progress(view)))) view = next;
    };
    await send();
    while (!paused && view.state === "RUNNING") {
      if (!entry.announced) {
        entry.announced = true; // once per draft, not on every page
        if (drafting.isTracked(entry.draft)) drafting.track(entry);
        toast(`${entry.title}: drafting goes on in the background.`);
      }
      const open = view.pages.filter((p) => p.state === "pending" || p.state === "drafted").length;
      const before = progress(view);
      const calls = view.preflight === "PASSED" ? Math.max(1, Math.min(WORKERS, open)) : 1;
      await Promise.all(Array.from({ length: calls }, send));
      if (view.state === "RUNNING" && progress(view) === before) await sleep(IDLE_MS); // calls still out elsewhere
    }
    if (paused) return; // picked up again on the next page
    drafting.untrack(entry.draft);
    finished(entry, view);
  }

  // Every draft this browser drives goes on here, except the one this drafting page drives itself.
  function setUpDrafting() {
    const page = document.querySelector("[data-analyzing]");
    const here = page ? page.dataset.template : null;
    for (const entry of tracked()) {
      if (entry.template === here) continue;
      drafting.hold(entry.draft, async (held) => {
        if (held) await carryOn(entry);
      });
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    setUpMenu();
    setUpFilters();
    setUpLibrary();
    setUpDrafting();
  });

  return { api, reason, toast, drafting };
})();
