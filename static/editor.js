// The field editor: one template's saved revision on its pages, edited in the browser and saved
// as a new revision through the state API. Selection, page, zoom and undo/redo live here; checks
// here are feedback only. Every save, review, approval and create is the server's, and is shown as
// the server answered.
"use strict";

(() => {
  const root = document.querySelector("[data-editor]");
  if (!root) return;
  const $ = (selector) => root.querySelector(selector);
  const $$ = (selector) => [...root.querySelectorAll(selector)];
  const data = JSON.parse($("[data-editor-data]").textContent);
  const id = data.template.id;
  const pages = data.pages;
  const byPage = new Map(pages.map((p) => [p.page, p]));
  const api = (method, path, body) => window.studio.api(method, `/api/templates/${id}${path}`, body);

  const TYPES = {
    signature: "Signature", initials: "Initials", date_signed: "Date signed", text: "Text",
    checkbox: "Checkbox", "text-merge": "Merge text", "checkbox-merge": "Merge checkbox",
  };
  const SIGNING = ["signature", "initials", "date_signed", "text", "checkbox"];
  const MERGES = ["text-merge", "checkbox-merge"];
  const MAPPINGS = {
    name: "Investor legal name", emailAddress: "Investor contact email", homePhone: "Contact phone",
    email: "Delivery email", amount: "Subscription amount", units: "Subscription units",
  };
  const SIZES = {
    signature: [180, 24], initials: [60, 20], date_signed: [100, 14], text: [160, 14],
    checkbox: [12, 12], "text-merge": [160, 14], "checkbox-merge": [12, 12],
  };
  const OPEN = ["CREATING", "VERIFYING", "NEEDS_STATUS_CHECK"];
  const INCOMPLETE = "the source's page inventory is INCOMPLETE; see its flags";
  const MIN = 4; // points: the smallest box a drag or resize leaves
  const HISTORY = 200;
  const EDGE = 1e-6;

  let saved = data.template; // the server's current revision
  let fields = clone(saved.fields); // the working copy
  let operation = data.operation;
  let undo = [];
  let redo = [];
  let selected = null; // a field id
  let page = pages.length ? pages[0].page : 1;
  let zoom = "fit";
  let filter = "all";
  let placing = null; // {signer, type, name} while the next page click adds a field
  let adding = { signer: 0, name: "" };
  let saving = false;
  let pending = null; // {content, base, id}: a save that may be retried with the same save_id
  let failure = null; // why the last save failed
  let conflict = null; // the server's current revision after a refused save
  let working = ""; // a review, approval, create or status check in flight
  let signOffError = "";

  function clone(value) { return JSON.parse(JSON.stringify(value)); }
  const round = (value) => Math.round(value * 100) / 100;
  const clamp = (value, low, high) => Math.max(low, Math.min(value, high));
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  const roles = () => data.roles.slice(0, saved.policy.countersigner ? 2 : 1);
  const role = (signer) => (signer === "sender" ? "Homebase prefill" : data.roles[signer] || `signer ${signer}`);
  const tag = (signer) => (signer === "sender" ? "M" : signer === 1 ? "S" : "C");
  const roleClass = (signer) => (signer === "sender" ? "role-sender" : `role-${signer}`);
  const label = (f) => `${TYPES[f.type] || f.type} · ${role(f.signer)}${f.name ? ` (${f.name})` : ""}`;
  const keyOf = (list) => JSON.stringify(list.map((f) => [
    f.id, f.document_index, f.page, f.x, f.y, f.width, f.height, f.type, f.signer, f.required, f.name ?? null,
  ]));
  const dirty = () => keyOf(fields) !== keyOf(saved.fields);
  const find = (fieldId) => fields.find((f) => f.id === fieldId) || null;
  const current = () => byPage.get(page);
  const placeable = (p) => p && p.rotation === 0 && p.width !== null;

  function newId() {
    const taken = new Set(fields.map((f) => f.id));
    let candidate;
    do {
      candidate = `f-${crypto.getRandomValues(new Uint32Array(2)).reduce((s, n) => s + n.toString(16).padStart(8, "0"), "")}`;
    } while (taken.has(candidate));
    return candidate;
  }

  // ---- checks (the server's rules, shown before a save; the server still decides) ----

  function problem(f) {
    const p = byPage.get(f.page);
    if (!p || f.document_index !== 0) return "the page is not in the source";
    if (p.rotation === null) return "the page's rotation is unknown, so a box on it cannot be placed";
    if (p.rotation) return "the page is rotated; fields cannot be placed on rotated pages yet";
    if (p.width === null) return "the page could not be measured";
    if (MERGES.includes(f.type)) {
      if (f.signer !== "sender") return "a merge field is filled by Homebase prefill";
      if (!(f.name in MAPPINGS)) return "choose a Homebase mapping";
    } else if (f.signer === "sender") {
      return "only merge fields are filled by Homebase prefill";
    } else if (!(f.signer >= 0 && f.signer < roles().length)) {
      return `${role(f.signer)} is not a signer in this policy`;
    }
    const box = [f.x, f.y, f.width, f.height];
    if (!box.every(Number.isFinite)) return "x, y, width and height are numbers";
    if (f.width <= 0 || f.height <= 0 || f.x < 0 || f.y < 0) return "the box has a positive size and starts on the page";
    if (f.x + f.width > p.width + EDGE || f.y + f.height > p.height + EDGE) return "the box lies outside the page";
    return "";
  }

  function invalid() { return fields.filter((f) => problem(f)); }

  function policyIssues(list) {
    if (!list.length) return ["the field map is empty"];
    const present = new Set(list.filter((f) => f.required).map((f) => `${f.signer}/${f.type}`));
    return saved.policy.required
      .filter((r) => !present.has(`${r.signer}/${r.type}`))
      .map((r) => `the policy requires a required ${r.type} field for ${data.roles[r.signer]}`);
  }

  function documentIssues() {
    const found = policyIssues(fields).map((message) => ({ where: "Policy", message }));
    if (saved.source.inventory !== "COMPLETE") found.unshift({ where: "Source", message: INCOMPLETE });
    for (const f of fields) {
      const message = problem(f);
      if (message) found.push({ where: `Page ${f.page} · ${label(f)}`, message, field: f.id });
    }
    return found;
  }

  // ---- changes and history ----

  function commit(next, nextSelected = selected) {
    if (keyOf(next) === keyOf(fields)) {
      selected = nextSelected;
      render();
      return;
    }
    undo.push({ fields, selected });
    if (undo.length > HISTORY) undo.shift();
    redo = [];
    fields = next;
    selected = nextSelected;
    render();
  }

  function change(fieldId, update) {
    commit(fields.map((f) => (f.id === fieldId ? update({ ...f }) : f)));
  }

  function step(from, to) {
    if (!from.length) return;
    to.push({ fields, selected });
    ({ fields, selected } = from.pop());
    if (selected && !find(selected)) selected = null;
    const f = selected && find(selected);
    if (f) page = f.page;
    render();
  }

  function adopt(snapshot) {
    saved = snapshot;
    fields = clone(snapshot.fields);
    undo = [];
    redo = [];
    pending = null;
    failure = null;
    conflict = null;
    if (selected && !find(selected)) selected = null;
  }

  function withSigner(f, value) {
    const signer = value === "sender" ? "sender" : Number(value);
    if (signer === "sender" && !MERGES.includes(f.type)) {
      return { ...f, signer, type: f.type === "checkbox" ? "checkbox-merge" : "text-merge", name: "" };
    }
    if (signer !== "sender" && MERGES.includes(f.type)) {
      const { name, ...rest } = f;
      return { ...rest, signer, type: f.type === "checkbox-merge" ? "checkbox" : "text" };
    }
    return { ...f, signer };
  }

  function addAt(x, y) {
    const p = current();
    if (!placeable(p)) {
      window.studio.toast("Fields cannot be placed on this page.");
      return;
    }
    const [width, height] = SIZES[placing.type];
    const f = {
      id: newId(), document_index: 0, page,
      x: round(clamp(x, 0, p.width - width)), y: round(clamp(y - height, 0, p.height - height)),
      width, height, type: placing.type, signer: placing.signer, required: placing.signer !== "sender",
    };
    if (MERGES.includes(f.type)) f.name = placing.name;
    placing = null;
    commit([...fields, f], f.id);
  }

  // ---- saving ----

  async function save() {
    if (saving || !dirty() || invalid().length || conflict) return;
    const content = JSON.stringify(fields);
    if (!pending || pending.content !== content || pending.base !== saved.revision) {
      pending = { content, base: saved.revision, id: crypto.randomUUID().replaceAll("-", "") };
    }
    saving = true;
    failure = null;
    render();
    const body = { base_revision: pending.base, policy: saved.policy, fields: JSON.parse(content), save_id: pending.id };
    const answer = await api("PUT", "", body);
    saving = false;
    if (answer.ok) {
      saved = answer.data.template;
      pending = null;
      window.studio.toast(answer.data.new_revision ? `Saved as revision ${saved.revision}.` : `Revision ${saved.revision} was already saved.`);
    } else if (answer.status === 409 && answer.data && answer.data.detail && answer.data.detail.current) {
      conflict = answer.data.detail.current;
      pending = null;
    } else {
      failure = answer.reason;
    }
    render();
  }

  // ---- review, approval and the simulated create ----

  async function signOff(kind) {
    working = kind;
    signOffError = "";
    renderPreflight();
    const answer = await api("POST", `/${kind}`, { revision: saved.revision, binding: saved.binding });
    working = "";
    if (answer.ok) {
      saved = answer.data;
    } else {
      signOffError = answer.reason;
      const latest = answer.status === 409 && answer.data && answer.data.detail && answer.data.detail.current;
      if (latest && latest.revision !== undefined && !dirty()) adopt(latest);
    }
    render();
    return answer.ok;
  }

  async function createTemplate() {
    working = "operation";
    signOffError = "";
    renderPreflight();
    const answer = await api("POST", "/operation", { revision: saved.revision, binding: saved.binding });
    working = "";
    if (answer.ok) {
      operation = answer.data;
    } else {
      signOffError = answer.reason;
      await readOperation();
    }
    render();
  }

  async function readOperation() {
    const answer = await api("GET", "/operation");
    if (answer.ok) operation = answer.data;
    else if (answer.status === 404) operation = null;
  }

  async function checkStatus() {
    working = "status";
    signOffError = "";
    renderPreflight();
    const answer = await api("POST", "/operation/status-check");
    working = "";
    if (answer.ok) operation = answer.data;
    else signOffError = answer.reason;
    render();
  }

  async function approveAndCreate() {
    if (!saved.approval && !(await signOff("approval"))) return;
    if (saved.approval) await createTemplate();
  }

  // ---- drawing ----

  function el(tagName, attributes = {}, ...children) {
    const node = document.createElement(tagName);
    for (const [key, value] of Object.entries(attributes)) {
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else if (value !== false && value !== null && value !== undefined) node.setAttribute(key, value === true ? "" : value);
    }
    node.append(...children);
    return node;
  }

  function scale() {
    const p = current();
    if (zoom !== "fit") return Number(zoom);
    const room = $("[data-canvas]").clientWidth - 64;
    return p && p.width ? clamp(room / p.width, 0.25, 3) : 1;
  }

  function render() {
    renderBar();
    renderBanners();
    renderNav();
    renderCanvas();
    renderInspector();
    if ($("[data-preflight]").open) renderPreflight();
  }

  function renderBar() {
    $("[data-revision]").textContent = `Rev ${saved.revision}`;
    const state = saving ? "saving" : conflict ? "conflict" : failure ? "failed" : dirty() ? "unsaved" : "saved";
    const text = {
      saving: "Saving…", conflict: "Not saved: changed elsewhere", failed: "Save failed",
      unsaved: "Unsaved changes", saved: "Saved",
    };
    $("[data-save-state]").dataset.state = state;
    $("[data-save-label]").textContent = text[state];
    const approval = $("[data-approval-label]");
    approval.textContent = saved.approval ? "Approved" : saved.review ? "Reviewed · not approved" : "Not reviewed";
    approval.dataset.state = saved.approval ? "approved" : saved.review ? "reviewed" : "none";
    const bad = invalid().length;
    const button = $("[data-save]");
    button.disabled = saving || !dirty() || bad > 0 || Boolean(conflict);
    button.title = bad ? `${plural(bad, "field")} cannot be saved as ${bad === 1 ? "it is" : "they are"}` : "Save (Ctrl/Cmd+S)";
    $("[data-discard]").hidden = !dirty() || saving;
    $("[data-undo]").disabled = !undo.length;
    $("[data-redo]").disabled = !redo.length;
    $("[data-add-toggle]").disabled = !placeable(current());
  }

  function banner(tone, title, body, ...actions) {
    return el("div", { class: `banner banner-${tone}`, role: tone === "red" ? "alert" : "status" },
      el("strong", { text: title }), el("span", { text: body }), ...actions);
  }

  function renderBanners() {
    const box = $("[data-banners]");
    const shown = [];
    if (conflict) {
      shown.push(banner("red", "Saved elsewhere.",
        `This template is now revision ${conflict.revision}. Your changes are not saved, and the studio does not save over a newer revision.`,
        el("button", { class: "button button-small", type: "button", text: `Discard mine and load revision ${conflict.revision}`, onclick: () => { adopt(conflict); render(); } })));
    } else if (failure) {
      shown.push(banner("red", "Save failed.", failure,
        el("button", { class: "button button-small", type: "button", text: "Retry", onclick: save })));
    }
    if (dirty() && saved.approval) {
      shown.push(banner("yellow", `Revision ${saved.revision} is approved.`,
        `Saving makes revision ${saved.revision + 1}, which needs a new review and approval.`));
    } else if (dirty() && saved.review) {
      shown.push(banner("yellow", `Revision ${saved.revision} is reviewed.`,
        `Saving makes revision ${saved.revision + 1}, which needs a new review.`));
    }
    const bad = invalid().length;
    if (bad) {
      shown.push(banner("yellow", `${plural(bad, "field")} cannot be saved yet.`, "Fix the fields marked with ! (see Issues), then save."));
    }
    box.replaceChildren(...shown);
  }

  function renderNav() {
    const counts = new Map();
    const flagged = new Set();
    for (const f of fields) {
      counts.set(f.page, (counts.get(f.page) || 0) + 1);
      if (problem(f)) flagged.add(f.page);
    }
    $("[data-coverage]").textContent = `${counts.size} of ${pages.length} with fields`;
    $$("[data-filter]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.filter === filter)));
    const items = pages
      .filter((p) => filter === "all" || counts.has(p.page) || p.page === page)
      .map((p) => {
        const n = counts.get(p.page) || 0;
        const note = !placeable(p) ? " · no fields can be placed" : "";
        return el("li", {},
          el("button", {
            type: "button", class: "page-button", "aria-current": p.page === page ? "page" : false,
            onclick: () => { page = p.page; selected = null; render(); },
          },
          el("strong", { text: `Page ${p.page}` }),
          el("span", { class: "small muted", text: `${n ? plural(n, "field") : "No fields"}${note}` }),
          flagged.has(p.page) ? el("span", { class: "issue-mark", text: "!", title: "A field here cannot be saved as it is" }) : ""));
      });
    $("[data-page-list]").replaceChildren(...items);
  }

  function renderCanvas() {
    const p = current();
    const s = scale();
    const sheet = $("[data-sheet]");
    const image = $("[data-sheet-image]");
    const width = (p && p.width) || 612;
    const height = (p && p.height) || 792;
    sheet.style.width = `${width * s}px`;
    sheet.style.height = `${height * s}px`;
    const src = `/api/sources/${saved.source_sha256}/pages/${page}.png`;
    if (image.getAttribute("src") !== src) {
      image.hidden = false;
      image.setAttribute("src", src);
      image.alt = `Page ${page} of the source`;
    }
    const note = $("[data-sheet-note]");
    note.hidden = placeable(p);
    note.textContent = !p ? "" : p.width === null ? "This page could not be measured, so no field can be placed on it."
      : "This page is rotated or its rotation is unknown, so no field can be placed on it yet.";
    $("[data-page-input]").value = String(page);
    $("[data-prev]").disabled = page <= pages[0].page;
    $("[data-next]").disabled = page >= pages[pages.length - 1].page;
    $$("[data-zoom]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.zoom === String(zoom))));
    $("[data-placing]").hidden = !placing;
    if (placing) $("[data-placing-text]").textContent = `Click the page to place ${TYPES[placing.type].toLowerCase()} for ${role(placing.signer)}. Esc cancels.`;
    sheet.classList.toggle("placing", Boolean(placing));

    const hadFocus = document.activeElement && document.activeElement.closest && document.activeElement.closest(".box");
    const boxes = fields.filter((f) => f.page === page).map((f) => {
      const message = problem(f);
      const node = el("div", {
        class: `box ${roleClass(f.signer)}${f.id === selected ? " selected" : ""}${message ? " problem" : ""}`,
        role: "button", tabindex: "0", "aria-pressed": String(f.id === selected),
        "aria-label": `${label(f)}${f.required ? ", required" : ", optional"}${message ? `: ${message}` : ""}`,
        "data-id": f.id, title: message || label(f),
        onpointerdown: (event) => drag(event, f.id, "move"),
        onfocus: () => { if (selected !== f.id) { selected = f.id; renderInspector(); markSelected(); } },
      }, el("span", { class: "box-tag", "aria-hidden": "true", text: tag(f.signer) }));
      if (message) node.append(el("span", { class: "box-issue", "aria-hidden": "true", text: "!" }));
      if (f.id === selected) {
        node.append(el("span", { class: "box-handle", "aria-hidden": "true", onpointerdown: (event) => drag(event, f.id, "resize") }));
      }
      place(node, f, s);
      return node;
    });
    $("[data-boxes]").replaceChildren(...boxes);
    if (hadFocus && selected) {
      const node = $(`.box[data-id="${selected}"]`);
      if (node) node.focus({ preventScroll: true });
    }
  }

  function place(node, f, s) {
    node.style.left = `${f.x * s}px`;
    node.style.top = `${f.y * s}px`;
    node.style.width = `${f.width * s}px`;
    node.style.height = `${f.height * s}px`;
  }

  function markSelected() {
    $$(".box").forEach((node) => {
      const on = node.dataset.id === selected;
      node.classList.toggle("selected", on);
      node.setAttribute("aria-pressed", String(on));
    });
  }

  function setValue(node, value) {
    if (document.activeElement !== node) node.value = value;
  }

  function options(select, list) {
    const values = list.map(([value]) => String(value));
    const now = [...select.options].map((o) => o.value);
    if (values.join("|") !== now.join("|")) {
      select.replaceChildren(...list.map(([value, text, disabled]) => el("option", { value: String(value), text, disabled })));
    }
  }

  function renderInspector() {
    const f = selected && find(selected);
    $("[data-field-panel]").hidden = !f;
    $("[data-page-panel]").hidden = Boolean(f);
    if (f) {
      const message = problem(f);
      $("[data-field-where]").textContent = `Field · page ${f.page}`;
      $("[data-field-title]").textContent = label(f);
      $("[data-field-id]").textContent = f.id;
      const alert = $("[data-field-problem]");
      alert.hidden = !message;
      alert.textContent = message ? `Cannot be saved as it is: ${message}.` : "";
      const signers = data.roles.map((name, index) => [index, `${name} (signer ${index})`, index >= roles().length]);
      options($('[data-prop="signer"]'), [...signers, ["sender", "Homebase prefill (sender)"]]);
      setValue($('[data-prop="signer"]'), String(f.signer));
      const kinds = f.signer === "sender" ? MERGES : SIGNING;
      options($('[data-prop="type"]'), kinds.map((k) => [k, TYPES[k]]));
      setValue($('[data-prop="type"]'), f.type);
      $("[data-merge-row]").hidden = !MERGES.includes(f.type);
      options($('[data-prop="name"]'), [["", "Choose from the approved list…"], ...Object.entries(MAPPINGS).map(([k, v]) => [k, `${v} · ${k}`])]);
      setValue($('[data-prop="name"]'), f.name || "");
      $("[data-merge-key]").textContent = f.name || "none";
      $$("[data-required]").forEach((b) => b.setAttribute("aria-pressed", String(String(f.required) === b.dataset.required)));
      for (const key of ["x", "y", "width", "height"]) setValue($(`[data-geom="${key}"]`), String(f[key]));
      return;
    }
    const p = current();
    $("[data-page-title]").textContent = `Page ${page} of ${pages.length}`;
    $("[data-page-dims]").textContent = p && p.width ? `${round(p.width)} × ${round(p.height)} pt` : "Size unknown";
    const here = fields.filter((x) => x.page === page);
    $("[data-page-empty]").hidden = here.length > 0;
    $("[data-page-fields]").replaceChildren(...here.map((x) => el("li", {},
      el("button", { type: "button", class: "list-button", onclick: () => { selected = x.id; render(); } },
        el("span", { class: `tag ${roleClass(x.signer)}`, text: tag(x.signer) }),
        el("span", { class: "grow", text: label(x) }),
        problem(x) ? el("span", { class: "issue-mark", text: "!" }) : ""))));
    const issues = documentIssues();
    $("[data-issues-title]").textContent = issues.length ? `Issues · ${issues.length}` : "Issues";
    $("[data-issues]").replaceChildren(...(issues.length ? issues.map((x) => el("li", {},
      x.field
        ? el("button", { type: "button", class: "list-button issue", onclick: () => { const f = find(x.field); page = f.page; selected = f.id; render(); } },
          el("strong", { text: x.where }), el("span", { text: x.message }))
        : el("span", { class: "issue" }, el("strong", { text: x.where }), el("span", { text: x.message }))))
      : [el("li", { class: "help", text: "No blocking issues in this revision." })]));
  }

  function fact(list, key, value, mono = false) {
    list.append(el("dt", { text: key }), el("dd", { class: mono ? "mono" : "", text: value }));
  }

  function renderPreflight() {
    const unsaved = dirty() || saving;
    $("[data-pf-subtitle]").textContent = `Revision ${saved.revision} · saved ${saved.saved_at.slice(0, 16).replace("T", " ")} UTC`;
    $("[data-pf-unsaved]").hidden = !unsaved;
    const identity = $("[data-pf-identity]");
    identity.replaceChildren();
    fact(identity, "Template", saved.id, true);
    fact(identity, "Revision", String(saved.revision));
    fact(identity, "Binding", `${saved.binding.slice(0, 16)}…`, true);
    fact(identity, "Source SHA-256", `${saved.source_sha256.slice(0, 16)}…`, true);
    fact(identity, "Signature mode", data.modes[saved.policy.countersigner ? "countersign" : "lp"]);
    fact(identity, "Sponsor", saved.details.sponsor_name || "Unassigned");
    fact(identity, "Title", saved.details.title || "Untitled");
    const byRole = new Map();
    const merges = new Map();
    const withFields = new Map();
    for (const f of saved.fields) {
      byRole.set(role(f.signer), (byRole.get(role(f.signer)) || 0) + 1);
      if (f.name) merges.set(f.name, (merges.get(f.name) || 0) + 1);
      withFields.set(f.page, (withFields.get(f.page) || 0) + 1);
    }
    const rolesList = $("[data-pf-roles]");
    rolesList.replaceChildren();
    if (!byRole.size) fact(rolesList, "Fields", "None");
    for (const [name, n] of byRole) fact(rolesList, name, String(n));
    const mergeList = $("[data-pf-merges]");
    mergeList.replaceChildren();
    if (!merges.size) fact(mergeList, "Merges", "None");
    for (const [name, n] of merges) fact(mergeList, `${MAPPINGS[name] || name}`, `${name} × ${n}`, true);
    $("[data-pf-pages-title]").textContent = `Pages with fields · ${withFields.size} of ${pages.length}`;
    $("[data-pf-pages]").replaceChildren(...[...withFields].sort((a, b) => a[0] - b[0]).map(([n, count]) => el("li", {},
      el("button", { type: "button", class: "list-button", onclick: () => { page = n; selected = null; $("[data-preflight]").close(); render(); } },
        el("strong", { text: `Page ${n}` }), el("span", { class: "grow muted", text: plural(count, "field") })))));
    $("[data-pf-issues]").replaceChildren(...(saved.issues.length
      ? saved.issues.map((x) => el("li", { class: "issue", text: x }))
      : [el("li", { class: "help", text: "No blocking issues." })]));
    renderOperation();

    const confirm = $("[data-pf-confirm]");
    confirm.checked = Boolean(saved.review);
    confirm.disabled = Boolean(saved.review) || unsaved || saved.issues.length > 0 || Boolean(working);
    $("[data-pf-confirm-label]").textContent = saved.review ? `Review confirmed for revision ${saved.revision}` : `I reviewed every field on revision ${saved.revision}`;
    $("[data-pf-confirm-help]").textContent = saved.review
      ? ` by ${saved.review.by}. A later save clears it.`
      : " Selecting, paging and zooming keep a confirmation; a saved change clears it.";
    const error = $("[data-pf-error]");
    error.hidden = !signOffError;
    error.textContent = signOffError;

    const open = operation && OPEN.includes(operation.state);
    const createdHere = operation && operation.state === "CREATED" && operation.revision === saved.revision;
    const approve = $("[data-pf-approve]");
    approve.hidden = Boolean(open);
    approve.textContent = working === "approval" ? "Approving…" : working === "operation" ? "Creating…"
      : createdHere ? "Created" : saved.approval ? "Create test template" : "Approve and create";
    approve.disabled = unsaved || Boolean(working) || saved.issues.length > 0 || !saved.review || createdHere
      || Boolean(operation && operation.orphans && operation.orphans.length);
    approve.title = !saved.review ? "Confirm the review first" : "";
    const check = $("[data-pf-check]");
    check.hidden = !open;
    check.disabled = Boolean(working);
    check.textContent = working === "status" ? "Checking…" : "Check status";
  }

  function renderOperation() {
    const box = $("[data-pf-operation]");
    const lines = [];
    if (!operation) {
      lines.push(el("span", { text: "Not created yet." }));
    } else {
      const state = operation.state.replaceAll("_", " ").toLowerCase();
      const ofThis = operation.revision === saved.revision ? "this revision" : `revision ${operation.revision}`;
      lines.push(el("span", {}, el("strong", { text: `${state[0].toUpperCase()}${state.slice(1)}` }),
        ` · ${ofThis} · operation `, el("span", { class: "mono", text: operation.id.slice(0, 12) })));
      if (operation.state === "CREATED") lines.push(el("span", { class: "help", text: "Simulated: recorded in the studio's storage; no provider was called." }));
      if (operation.state === "NEEDS_STATUS_CHECK") lines.push(el("span", { class: "help", text: "The outcome is unknown. Check its status; a new create is refused until it resolves." }));
      if (operation.note) lines.push(el("span", { class: "help", text: operation.note }));
      if (operation.warning) lines.push(el("div", { class: "alert alert-yellow", text: operation.warning }));
    }
    box.replaceChildren(...lines);
  }

  // ---- pointer, keyboard and controls ----

  function drag(event, fieldId, mode) {
    if (event.button !== 0 || placing) return;
    event.preventDefault();
    event.stopPropagation();
    if (selected !== fieldId) {
      selected = fieldId;
      render();
    }
    const start = { x: event.clientX, y: event.clientY, f: { ...find(fieldId) } };
    const p = byPage.get(start.f.page);
    const s = scale();
    let moved = null;
    const node = () => $(`.box[data-id="${fieldId}"]`);
    const onMove = (e) => {
      if (!moved && Math.abs(e.clientX - start.x) + Math.abs(e.clientY - start.y) < 3) return;
      const dx = (e.clientX - start.x) / s;
      const dy = (e.clientY - start.y) / s;
      const f = start.f;
      moved = mode === "move"
        ? { x: round(clamp(f.x + dx, 0, p.width - f.width)), y: round(clamp(f.y + dy, 0, p.height - f.height)) }
        : { width: round(clamp(f.width + dx, MIN, p.width - f.x)), height: round(clamp(f.height + dy, MIN, p.height - f.y)) };
      const target = node();
      if (target) place(target, { ...f, ...moved }, s);
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      if (moved) change(fieldId, (f) => ({ ...f, ...moved }));
      const target = node();
      if (target) target.focus({ preventScroll: true });
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
  }

  function nudge(key, shift, alt) {
    const f = find(selected);
    const p = byPage.get(f.page);
    if (!placeable(p)) return;
    const d = shift ? 10 : 1;
    const dx = key === "ArrowLeft" ? -d : key === "ArrowRight" ? d : 0;
    const dy = key === "ArrowUp" ? -d : key === "ArrowDown" ? d : 0;
    change(f.id, (x) => (alt
      ? { ...x, width: round(clamp(x.width + dx, MIN, p.width - x.x)), height: round(clamp(x.height + dy, MIN, p.height - x.y)) }
      : { ...x, x: round(clamp(x.x + dx, 0, p.width - x.width)), y: round(clamp(x.y + dy, 0, p.height - x.height)) }));
  }

  function remove(fieldId) {
    commit(fields.filter((f) => f.id !== fieldId), null);
  }

  function goTo(number) {
    const target = clamp(Math.round(number) || page, pages[0].page, pages[pages.length - 1].page);
    if (target !== page) {
      page = target;
      selected = null;
      placing = null;
    }
    render();
    $("[data-canvas]").scrollTop = 0;
  }

  function renderAddMenu() {
    const signers = [...data.roles.map((name, index) => [index, name, index >= roles().length]), ["sender", "Homebase prefill"]];
    $("[data-recipients]").replaceChildren(...signers.map(([value, name, disabled]) => el("button", {
      type: "button", role: "radio", class: "recipient", "aria-checked": String(adding.signer === value), disabled,
      title: disabled ? "This policy has no CounterSigner" : false,
      onclick: () => { adding = { ...adding, signer: value }; renderAddMenu(); },
    }, el("span", { class: `tag ${roleClass(value)}`, text: tag(value) }), ` ${name}`)));
    const merge = adding.signer === "sender";
    $("[data-add-merge-row]").hidden = !merge;
    const select = $("[data-add-merge]");
    options(select, [["", "Choose from the approved list…"], ...Object.entries(MAPPINGS).map(([k, v]) => [k, `${v} · ${k}`])]);
    select.value = adding.name;
    const kinds = merge ? MERGES : SIGNING;
    $("[data-add-types]").replaceChildren(...kinds.map((kind) => el("button", {
      type: "button", class: "button button-small", text: TYPES[kind], disabled: merge && !adding.name,
      title: merge && !adding.name ? "Choose a Homebase mapping first" : false,
      onclick: () => { placing = { signer: adding.signer, type: kind, name: adding.name }; closeAddMenu(); render(); },
    })));
  }

  function closeAddMenu() {
    $("[data-add-menu]").hidden = true;
    $("[data-add-toggle]").setAttribute("aria-expanded", "false");
  }

  const typing = (target) => target && ["INPUT", "SELECT", "TEXTAREA"].includes(target.tagName);

  document.addEventListener("keydown", (event) => {
    if ($("[data-preflight]").open || $("[data-discard-dialog]").open) return;
    const mod = event.metaKey || event.ctrlKey;
    if (mod && event.key.toLowerCase() === "s") {
      event.preventDefault();
      save();
      return;
    }
    if (typing(event.target)) return;
    if (mod && event.key.toLowerCase() === "z") {
      event.preventDefault();
      if (event.shiftKey) step(redo, undo);
      else step(undo, redo);
      return;
    }
    if (mod && event.key.toLowerCase() === "y") {
      event.preventDefault();
      step(redo, undo);
      return;
    }
    if (event.key === "Escape") {
      if (placing) placing = null;
      else if (!$("[data-add-menu]").hidden) closeAddMenu();
      else selected = null;
      render();
      return;
    }
    if (event.key === "PageUp" || event.key === "PageDown") {
      event.preventDefault();
      goTo(page + (event.key === "PageUp" ? -1 : 1));
      return;
    }
    if (!selected || !find(selected) || mod) return;
    if (event.key.startsWith("Arrow")) {
      event.preventDefault();
      nudge(event.key, event.shiftKey, event.altKey);
    } else if (event.key === "Delete" || event.key === "Backspace") {
      event.preventDefault();
      remove(selected);
    }
  });

  $("[data-sheet]").addEventListener("click", (event) => {
    if (event.target.closest(".box")) return;
    if (placing) {
      const rect = $("[data-sheet]").getBoundingClientRect();
      const s = scale();
      addAt((event.clientX - rect.left) / s, (event.clientY - rect.top) / s);
    } else if (selected) {
      selected = null;
      render();
    }
  });
  $("[data-sheet-image]").addEventListener("error", (event) => {
    event.target.hidden = true;
    const note = $("[data-sheet-note]");
    note.hidden = false;
    note.textContent = "This page could not be shown. Reload to try again.";
  });
  $("[data-cancel-place]").addEventListener("click", () => { placing = null; render(); });
  $("[data-prev]").addEventListener("click", () => goTo(page - 1));
  $("[data-next]").addEventListener("click", () => goTo(page + 1));
  $("[data-page-input]").addEventListener("change", (event) => goTo(Number(event.target.value)));
  $$("[data-zoom]").forEach((b) => b.addEventListener("click", () => { zoom = b.dataset.zoom === "fit" ? "fit" : Number(b.dataset.zoom); render(); }));
  $$("[data-filter]").forEach((b) => b.addEventListener("click", () => { filter = b.dataset.filter; render(); }));
  $("[data-undo]").addEventListener("click", () => step(undo, redo));
  $("[data-redo]").addEventListener("click", () => step(redo, undo));
  $("[data-save]").addEventListener("click", save);
  $("[data-add-toggle]").addEventListener("click", (event) => {
    event.stopPropagation();
    const menu = $("[data-add-menu]");
    const opening = menu.hidden;
    menu.hidden = !opening;
    event.currentTarget.setAttribute("aria-expanded", String(opening));
    if (opening) renderAddMenu();
  });
  $("[data-add-menu]").addEventListener("click", (event) => event.stopPropagation());
  $("[data-add-merge]").addEventListener("change", (event) => { adding = { ...adding, name: event.target.value }; renderAddMenu(); });
  document.addEventListener("click", () => { if (!$("[data-add-menu]").hidden) closeAddMenu(); });

  $("[data-deselect]").addEventListener("click", () => { selected = null; render(); });
  $('[data-prop="signer"]').addEventListener("change", (event) => change(selected, (f) => withSigner(f, event.target.value)));
  $('[data-prop="type"]').addEventListener("change", (event) => change(selected, (f) => ({ ...f, type: event.target.value })));
  $('[data-prop="name"]').addEventListener("change", (event) => change(selected, (f) => ({ ...f, name: event.target.value })));
  $$("[data-required]").forEach((b) => b.addEventListener("click", () => change(selected, (f) => ({ ...f, required: b.dataset.required === "true" }))));
  $$("[data-geom]").forEach((input) => input.addEventListener("change", () => {
    const value = Number(input.value.trim());
    if (input.value.trim() === "" || !Number.isFinite(value)) {
      input.value = String(find(selected)[input.dataset.geom]);
      return;
    }
    change(selected, (f) => ({ ...f, [input.dataset.geom]: round(value) }));
  }));
  $("[data-delete]").addEventListener("click", () => remove(selected));

  const discardDialog = $("[data-discard-dialog]");
  $("[data-discard]").addEventListener("click", () => discardDialog.showModal());
  $("[data-discard-cancel]").addEventListener("click", () => discardDialog.close());
  $("[data-discard-confirm]").addEventListener("click", () => {
    discardDialog.close();
    adopt(saved);
    render();
  });

  const preflight = $("[data-preflight]");
  $("[data-open-preflight]").addEventListener("click", async () => {
    signOffError = "";
    renderPreflight();
    preflight.showModal();
    await readOperation();
    renderPreflight();
  });
  $$("[data-pf-close]").forEach((b) => b.addEventListener("click", () => preflight.close()));
  $("[data-pf-confirm]").addEventListener("change", (event) => {
    if (event.target.checked) signOff("review");
  });
  $("[data-pf-approve]").addEventListener("click", approveAndCreate);
  $("[data-pf-check]").addEventListener("click", checkStatus);

  window.addEventListener("resize", () => { if (zoom === "fit") renderCanvas(); });
  window.addEventListener("beforeunload", (event) => {
    if (dirty() || saving || working) event.preventDefault();
  });

  render();
})();
