// Setup: the sponsor account, one sealed PDF and the signing policy, then a new draft template.
//
// The PDF is hashed here, then either PUT straight to R2 through a presigned URL and sealed
// (hosted), or POSTed to the studio (local). The server re-hashes and inventories it; only the
// sealed record it answers is shown. Nothing here is sample data.
"use strict";

(() => {
  const root = document.querySelector("[data-setup]");
  if (!root) return;
  const $ = (selector) => root.querySelector(selector);
  const direct = root.dataset.upload === "direct";
  const maxBytes = Number(root.dataset.maxBytes);
  const ACCOUNT_ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]*$/;
  const MODE_NOTES = {
    lp: "LP signature only: sponsor fields are left out. Those pages stay in the document.",
    countersign: "Role indices identify recipients. They do not set the signing order.",
  };
  const TONES = {
    hashing: "blue", requesting: "blue", uploading: "blue", verifying: "blue",
    ready: "green", expired: "yellow", failed: "red", unsupported: "red",
  };

  const state = { file: null, phase: "idle", percent: 0, message: "", source: null, xhr: null, run: 0 };
  let creating = false;

  // ---- the file ----

  function choose(file) {
    cancelTransfer();
    state.run += 1;
    Object.assign(state, { file, source: null, percent: 0, message: "" });
    const isPdf = file.type === "application/pdf" || /\.pdf$/i.test(file.name);
    if (!isPdf) return show("unsupported", "Only PDF files are supported.");
    if (file.size > maxBytes) {
      return show("unsupported", `This file is ${mib(file.size)}; a source is at most ${mib(maxBytes)}.`);
    }
    if (file.size === 0) return show("unsupported", "This file is empty.");
    transfer(state.run);
  }

  async function transfer(run) {
    show("hashing", "Computing the file's SHA-256 here, before anything is sent.");
    let sha256;
    try {
      const digest = await crypto.subtle.digest("SHA-256", await state.file.arrayBuffer());
      sha256 = [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
    } catch {
      return run === state.run && show("failed", "The file could not be read in this browser.");
    }
    if (run !== state.run) return;
    let sealed;
    if (direct) {
      show("requesting", "Requesting a private upload link.");
      const link = await studio.api("POST", "/api/uploads", { sha256, size: state.file.size });
      if (run !== state.run) return;
      if (!link.ok) return show(outcome(link.status), link.reason);
      show("uploading", "Uploading to private storage.");
      const put = await send("PUT", link.data.url, run);
      if (run !== state.run || put === null) return;
      if (put.status === 403) {
        return show("expired", "The upload link was refused, most likely because it expired.");
      }
      if (put.status < 200 || put.status >= 300) {
        return show("failed", `Storage answered ${put.status || "nothing"}; nothing was sealed.`);
      }
      show("verifying", "The studio is re-hashing the upload and checking every page before sealing.");
      sealed = await studio.api("POST", `/api/uploads/${sha256}/seal`);
    } else {
      show("uploading", "Sending to the studio on this machine.");
      const post = await send("POST", "/api/sources", run);
      if (run !== state.run || post === null) return;
      sealed = post.status === 201
        ? { ok: true, data: JSON.parse(post.text) }
        : { ok: false, status: post.status, reason: studio.reason(post.status, post.text) };
    }
    if (run !== state.run) return;
    if (!sealed.ok) return show(outcome(sealed.status), sealed.reason);
    if (sealed.data.sha256 !== sha256) {
      return show("failed", "The studio sealed different bytes than this file. Try again.");
    }
    state.source = sealed.data;
    show("ready", "Sealed. The studio re-hashed the bytes and measured every page.");
  }

  // A refused file (too large, not a readable PDF) fails the same way every time, so no retry.
  function outcome(status) {
    return status === 413 || status === 422 ? "unsupported" : "failed";
  }

  // XMLHttpRequest, because fetch cannot report upload progress.
  function send(method, url, run) {
    return new Promise((resolve) => {
      const xhr = new XMLHttpRequest();
      state.xhr = xhr;
      xhr.open(method, url);
      xhr.setRequestHeader("content-type", "application/pdf");
      xhr.upload.addEventListener("progress", (event) => {
        if (run !== state.run || !event.lengthComputable) return;
        state.percent = Math.round((100 * event.loaded) / event.total);
        render();
      });
      xhr.addEventListener("load", () => resolve({ status: xhr.status, text: xhr.responseText }));
      xhr.addEventListener("error", () => resolve({ status: 0, text: "" }));
      xhr.addEventListener("abort", () => resolve(null));
      xhr.send(state.file);
    });
  }

  function cancelTransfer() {
    if (state.xhr) state.xhr.abort();
    state.xhr = null;
  }

  function reset() {
    cancelTransfer();
    state.run += 1;
    Object.assign(state, { file: null, phase: "idle", source: null, percent: 0, message: "" });
    $("[data-file-input]").value = "";
    render();
  }

  function show(phase, message) {
    state.phase = phase;
    state.message = message;
    render();
  }

  // ---- the policy and details ----

  function mode() {
    return root.querySelector("[data-mode]:checked").value;
  }

  function policy() {
    const countersigner = mode() === "countersign";
    const required = [...root.querySelectorAll("[data-required]:checked")]
      .map((box) => ({ signer: Number(box.dataset.signer), type: box.dataset.type }))
      .filter((item) => countersigner || item.signer === 0);
    return { countersigner, required };
  }

  function defaultTitle() {
    const sponsor = $("[data-sponsor-name]").value.trim();
    const stem = state.file ? state.file.name.replace(/\.pdf$/i, "").replace(/[_-]+/g, " ").trim() : "";
    const name = stem ? stem[0].toUpperCase() + stem.slice(1) : "";
    return [sponsor, name].filter(Boolean).join(" - ");
  }

  function details() {
    return {
      title: $("[data-title]").value.trim() || defaultTitle(),
      sponsor_name: $("[data-sponsor-name]").value.trim(),
      sponsor_account_id: $("[data-sponsor-id]").value.trim(),
    };
  }

  function blockers() {
    const typed = details();
    const reasons = [];
    if (!typed.sponsor_name) reasons.push("Type the sponsor account name");
    if (typed.sponsor_account_id && !ACCOUNT_ID.test(typed.sponsor_account_id)) {
      reasons.push("Account ID: letters, digits, '_', '.', ':' or '-'");
    }
    if (state.phase !== "ready") reasons.push("Upload a PDF and wait until it is sealed");
    if (!typed.title) reasons.push("Add a template title");
    return reasons;
  }

  // ---- rendering ----

  const LABELS = {
    hashing: "Checking file", requesting: "Requesting link", uploading: "Uploading",
    verifying: "Verifying", ready: "Sealed", expired: "Link expired", failed: "Failed",
    unsupported: "Unsupported",
  };

  function render() {
    const hasFile = state.file !== null;
    $("[data-drop]").hidden = hasFile;
    $("[data-file-card]").hidden = !hasFile;
    if (hasFile) renderFile();
    renderPolicy();
    renderSummary();
  }

  function renderFile() {
    const busy = ["hashing", "requesting", "uploading", "verifying"].includes(state.phase);
    $("[data-file-name]").textContent = state.file.name;
    $("[data-file-meta]").textContent = mib(state.file.size);
    const chip = $("[data-file-state]");
    chip.className = `chip tone-${TONES[state.phase] || "gray"}`;
    chip.textContent = state.phase === "uploading" ? `Uploading ${state.percent}%` : LABELS[state.phase] || "";
    const bar = $("[data-progress]");
    bar.hidden = !busy;
    const shown = state.phase === "uploading" ? state.percent : state.phase === "verifying" ? 100 : 0;
    bar.setAttribute("aria-valuenow", String(shown));
    bar.firstElementChild.style.width = `${shown}%`;
    $("[data-file-message]").textContent = state.message;
    const facts = $("[data-file-facts]");
    const flags = $("[data-file-flags]");
    facts.replaceChildren();
    flags.replaceChildren();
    facts.hidden = flags.hidden = !state.source;
    if (state.source) {
      const sha = state.source.sha256;
      fact(facts, "SHA-256", `${sha.slice(0, 12)}…${sha.slice(-4)}`, "mono");
      fact(facts, "Pages", `${state.source.pages.length}`);
      fact(facts, "Inventory", state.source.inventory === "COMPLETE"
        ? "Complete: every page was read"
        : "INCOMPLETE: review and approval will stay blocked");
      for (const flag of state.source.flags) {
        const item = document.createElement("li");
        item.textContent = (flag.page ? `Page ${flag.page}: ` : "") + flag.reason;
        flags.append(item);
      }
      flags.hidden = state.source.flags.length === 0;
    }
    const actions = $("[data-file-actions]");
    actions.replaceChildren();
    if (busy) actions.append(button("Cancel upload", reset));
    if (state.phase === "expired") actions.append(button("Request a fresh link", () => choose(state.file)));
    if (state.phase === "failed") actions.append(button("Retry upload", () => choose(state.file)));
    if (!busy) actions.append(button(state.phase === "ready" ? "Replace file" : "Choose another PDF", pick));
  }

  function renderPolicy() {
    const countersigner = mode() === "countersign";
    root.querySelectorAll("[data-countersigner-only]").forEach((label) => {
      label.hidden = !countersigner;
    });
    $("[data-mode-note]").textContent = MODE_NOTES[mode()];
    $("[data-title]").placeholder = defaultTitle() || "Defaults from the account and file name";
  }

  function renderSummary() {
    const typed = details();
    const current = policy();
    const summary = $("[data-summary]");
    summary.replaceChildren();
    const account = typed.sponsor_name
      ? typed.sponsor_name + (typed.sponsor_account_id ? ` (${typed.sponsor_account_id})` : "")
      : "Not named yet";
    fact(summary, "Sponsor", account);
    fact(summary, "Document", state.file ? state.file.name : "None yet");
    fact(summary, "Pages", state.source ? String(state.source.pages.length) : "–");
    fact(summary, "Policy", current.countersigner ? "LP + sponsor countersignature" : "LP signature only");
    fact(summary, "Roles", current.countersigner ? "Client · 0, CounterSigner · 1" : "Client · 0");
    fact(summary, "Required", current.required.length
      ? current.required.map((r) => `${r.signer ? "CounterSigner" : "Client"} ${r.type.replace("_", " ")}`).join(", ")
      : "Nothing");
    const reasons = blockers();
    const list = $("[data-blockers]");
    list.replaceChildren(...reasons.map((text) => {
      const item = document.createElement("li");
      item.textContent = text;
      return item;
    }));
    $("[data-create]").disabled = creating || reasons.length > 0;
  }

  function fact(list, term, value, className) {
    const dt = document.createElement("dt");
    const dd = document.createElement("dd");
    dt.textContent = term;
    dd.textContent = value;
    if (className) dd.className = className;
    list.append(dt, dd);
  }

  function button(label, onClick) {
    const element = document.createElement("button");
    element.type = "button";
    element.className = "button button-small";
    element.textContent = label;
    element.addEventListener("click", onClick);
    return element;
  }

  function mib(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1048576) return `${Math.round(bytes / 1024)} KiB`;
    return `${(bytes / 1048576).toFixed(bytes < 10485760 ? 1 : 0)} MiB`;
  }

  function pick() {
    $("[data-file-input]").click();
  }

  // ---- create ----

  async function create() {
    if (blockers().length || creating) return;
    creating = true;
    const error = $("[data-create-error]");
    error.hidden = true;
    renderSummary();
    const body = { source_sha256: state.source.sha256, policy: policy(), details: details() };
    const answer = await studio.api("POST", "/api/templates", body);
    if (answer.ok) {
      window.location.assign(`/templates/${answer.data.id}`);
      return;
    }
    creating = false;
    error.textContent = answer.status === 503 || answer.status === 0
      ? `${answer.reason} Check the library for a new draft before creating again.`
      : answer.reason;
    error.hidden = false;
    renderSummary();
  }

  // ---- wiring ----

  const input = $("[data-file-input]");
  input.addEventListener("change", () => { if (input.files[0]) choose(input.files[0]); });
  $("[data-pick]").addEventListener("click", pick);
  const drop = $("[data-drop]");
  drop.addEventListener("dragover", (event) => { event.preventDefault(); drop.classList.add("dragging"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("dragging"));
  drop.addEventListener("drop", (event) => {
    event.preventDefault();
    drop.classList.remove("dragging");
    const file = event.dataTransfer.files[0];
    if (file) choose(file);
  });
  root.addEventListener("input", render);
  root.addEventListener("change", (event) => { if (event.target !== input) render(); });
  $("[data-create]").addEventListener("click", create);

  const dialog = $("[data-discard-dialog]");
  root.querySelectorAll("[data-cancel]").forEach((link) => {
    link.addEventListener("click", (event) => {
      const started = state.file !== null || $("[data-sponsor-name]").value.trim() !== "";
      if (!started) return;
      event.preventDefault();
      dialog.showModal();
    });
  });
  $("[data-dialog-cancel]").addEventListener("click", () => dialog.close());
  $("[data-dialog-discard]").addEventListener("click", () => {
    reset();
    window.location.assign("/");
  });
  window.addEventListener("beforeunload", (event) => {
    if (["requesting", "uploading", "verifying"].includes(state.phase)) event.preventDefault();
  });

  render();
})();
