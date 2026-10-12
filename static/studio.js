// Shared page behaviour: the account menu, signing out, filter auto-submit, and the toast.
"use strict";

window.studio = (() => {
  let toastTimer = null;

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
    document.querySelectorAll("[data-autosubmit]").forEach((select) => {
      select.addEventListener("change", () => select.form.requestSubmit());
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    setUpMenu();
    setUpFilters();
  });

  return { api, reason, toast };
})();
