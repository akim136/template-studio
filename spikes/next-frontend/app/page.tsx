// The library (templates/library.html and pages.library) as a Server Component. The view logic is
// a port of studio/ui/pages.py; the tab and tone of each status label duplicate
// studio/state/lifecycle.py, because the API returns only the label.
import type { Metadata } from "next";
import { AutoSubmitSelect } from "./auto-submit";
import { summaries, type Summary } from "./studio";

export const metadata: Metadata = { title: "Templates" };

const PAGE_SIZE = 10;
const MODES: [string, string][] = [
  ["lp", "LP signature only"],
  ["countersign", "LP + sponsor countersignature"],
];
const SORTS: Record<string, "asc" | "desc"> = { updated: "desc", title: "asc" };
const TABS: [string, string][] = [
  ["all", "All templates"],
  ["drafts", "Drafts"],
  ["review", "Needs review"],
  ["completed", "Completed"],
  ["failed", "Failed"],
];
const STATUS: Record<string, { tab: string; tone: string }> = {
  Draft: { tab: "drafts", tone: "gray" },
  "Needs review": { tab: "review", tone: "yellow" },
  Approved: { tab: "review", tone: "blue" },
  Creating: { tab: "review", tone: "blue" },
  Verifying: { tab: "review", tone: "blue" },
  "Needs status check": { tab: "failed", tone: "yellow" },
  Completed: { tab: "completed", tone: "green" },
  Failed: { tab: "failed", tone: "red" },
  Unreadable: { tab: "failed", tone: "red" },
};
const MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(" ");

type Row = {
  id: string;
  title: string;
  sponsorName: string;
  sponsorAccountId: string;
  countersigner: boolean | null;
  status: { label: string; tab: string; tone: string };
  updated: string;
  attribution: string;
  error?: string | null;
};

function row(summary: Summary): Row {
  const details = summary.details ?? {};
  const { approval, review } = summary;
  const attribution = approval
    ? `Reviewed by ${review?.by} · approved by ${approval.by}`
    : review
      ? `Reviewed by ${review.by}`
      : "Not reviewed";
  return {
    id: summary.id,
    title: details.title || "Untitled",
    sponsorName: details.sponsor_name ?? "",
    sponsorAccountId: details.sponsor_account_id ?? "",
    countersigner: summary.countersigner ?? null, // an unreadable template has none
    status: { label: summary.status, ...(Object.hasOwn(STATUS, summary.status) ? STATUS[summary.status] : STATUS.Unreadable) },
    updated: summary.saved_at ?? "",
    attribution,
    error: summary.error,
  };
}

function when(stamp: string): string {
  if (!stamp) return "Not recorded";
  const moment = new Date(stamp);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${MONTHS[moment.getUTCMonth()]} ${moment.getUTCDate()}, ${pad(moment.getUTCHours())}:${pad(moment.getUTCMinutes())} UTC`;
}

function modeLabel(countersigner: boolean): string {
  return MODES[countersigner ? 1 : 0][1];
}

// Python's casefold, closely enough for this page: upper-casing first turns "ß" into "SS".
function fold(text: string): string {
  return text.toUpperCase().toLowerCase();
}

function one(value: string | string[] | undefined): string {
  return (Array.isArray(value) ? value[0] : value) ?? "";
}

export default async function Library({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const tab = TABS.some(([key]) => key === one(params.tab)) ? one(params.tab) : "all";
  const mode = MODES.some(([key]) => key === one(params.mode)) ? one(params.mode) : "all";
  // Own keys only: `in` would accept inherited names such as "constructor".
  const sort = Object.hasOwn(SORTS, one(params.sort)) ? one(params.sort) : "updated";
  const order = ["asc", "desc"].includes(one(params.order)) ? one(params.order) : SORTS[sort];
  const q = one(params.q).split(/\s+/).filter(Boolean).join(" ").slice(0, 200);
  const view = { tab, q, mode, sort, order };

  const loaded = await summaries();
  if (loaded === null) {
    return (
      <main className="page page-library">
        <div className="card table-card">
          <div className="empty" role="alert">
            <strong>Templates could not be loaded</strong>
            <span>Something went wrong reading saved templates. Nothing was changed.</span>
            <a className="button button-small" href="/">
              Retry
            </a>
          </div>
        </div>
      </main>
    );
  }
  const rows = loaded.map(row);
  const needle = fold(q);
  const matching = rows.filter((r) => {
    if (mode !== "all" && (r.countersigner === null || r.countersigner !== (mode !== "lp"))) return false;
    const haystack = [r.title, r.id, r.sponsorName, r.sponsorAccountId].join(" ");
    return !needle || fold(haystack).includes(needle);
  });
  const counts: Record<string, number> = Object.fromEntries(TABS.map(([key]) => [key, 0]));
  for (const r of matching) {
    counts.all += 1;
    counts[r.status.tab] += 1;
  }
  const shown = matching.filter((r) => tab === "all" || r.status.tab === tab);
  const key = sort === "title" ? (r: Row) => fold(r.title) : (r: Row) => r.updated;
  shown.sort((a, b) => (key(a) < key(b) ? -1 : key(a) > key(b) ? 1 : 0) * (order === "desc" ? -1 : 1));
  const pagesTotal = Math.max(1, Math.ceil(shown.length / PAGE_SIZE));
  const page = Math.min(Math.max(Number.parseInt(one(params.page), 10) || 1, 1), pagesTotal);
  const start = (page - 1) * PAGE_SIZE;
  const visible = shown.slice(start, start + PAGE_SIZE);

  const link = (changes: Record<string, string | number>) => {
    const merged: Record<string, string | number> = { ...view, page, ...changes };
    const kept = Object.entries(merged).filter(([, v]) => !["", "all", 1].includes(v));
    return kept.length ? `/?${new URLSearchParams(kept.map(([k, v]) => [k, String(v)]))}` : "/";
  };
  const ariaSort = (column: string) =>
    sort === column ? (order === "asc" ? "ascending" : "descending") : "none";
  const arrow = (column: string) => (sort === column ? (order === "asc" ? " ↑" : " ↓") : "");

  return (
    <main className="page page-library" data-library>
      <div className="title-row">
        <div>
          <h1>Templates</h1>
          <p className="subline">
            {rows.length} template{rows.length === 1 ? "" : "s"}
          </p>
        </div>
        <a className="button button-primary" href="/new">
          New template
        </a>
      </div>

      <form className="filters" method="get" action="/">
        <nav className="tabs" aria-label="Status filter">
          {TABS.map(([key, label]) => (
            <a key={key} className="tab" href={link({ tab: key, page: 1 })} aria-current={tab === key ? "page" : undefined}>
              <span>{label}</span>
              <span className="count">{counts[key]}</span>
            </a>
          ))}
        </nav>
        <span className="grow"></span>
        {tab !== "all" ? <input type="hidden" name="tab" value={tab} /> : null}
        {sort !== "updated" ? <input type="hidden" name="sort" value={sort} /> : null}
        <input type="hidden" name="order" value={order} />
        <div className="search">
          <input
            className="input"
            type="search"
            name="q"
            defaultValue={q}
            aria-label="Search templates by title, sponsor or account ID"
            placeholder="Search title, sponsor, or account ID"
          />
        </div>
        <AutoSubmitSelect
          name="mode"
          value={mode}
          label="Signature mode filter"
          options={[["all", "All signature modes"], ...MODES]}
        />
      </form>

      <div className="card table-card">
        {rows.length === 0 ? (
          <div className="empty">
            <strong>No templates yet</strong>
            <span>Start with New template.</span>
          </div>
        ) : visible.length === 0 ? (
          <div className="empty">
            <strong>No templates match these filters</strong>
            <span>
              {q ? `Nothing matches “${q}” here. ` : ""}Try another status or signature mode.
            </span>
            <a className="button button-small" href="/">
              Clear filters
            </a>
          </div>
        ) : (
          <>
            <div className="table-scroll">
              <table className="templates">
                <thead>
                  <tr>
                    <th aria-sort={ariaSort("title")}>
                      <a href={link({ sort: "title", order: sort === "title" && order === "asc" ? "desc" : "asc", page: 1 })}>
                        Template{arrow("title")}
                      </a>
                    </th>
                    <th>Sponsor account</th>
                    <th>Signature mode</th>
                    <th>Status</th>
                    <th aria-sort={ariaSort("updated")}>
                      <a href={link({ sort: "updated", order: sort === "updated" && order === "desc" ? "asc" : "desc", page: 1 })}>
                        Updated{arrow("updated")}
                      </a>
                    </th>
                    <th>Attribution</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((r) => (
                    <tr key={r.id}>
                      <td>
                        <div className="cell-stack">
                          <a className="open" href={`/templates/${r.id}`}>
                            {r.title}
                          </a>
                          <span className="cell-sub">{r.id}</span>
                        </div>
                      </td>
                      <td>
                        {r.sponsorName ? (
                          <div className="cell-stack">
                            <span>{r.sponsorName}</span>
                            <span className="cell-sub">{r.sponsorAccountId || "No account ID"}</span>
                          </div>
                        ) : (
                          <span className="unassigned">Unassigned</span>
                        )}
                      </td>
                      <td>{r.countersigner === null ? <span className="unassigned">Unknown</span> : modeLabel(r.countersigner)}</td>
                      <td>
                        <span className={`chip tone-${r.status.tone}`} title={r.error ?? undefined}>
                          {r.status.label}
                        </span>
                      </td>
                      <td className="updated">{when(r.updated)}</td>
                      <td className="attribution">{r.attribution}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="pager">
              <span>
                Showing {start + 1}–{Math.min(start + PAGE_SIZE, shown.length)} of {shown.length}
              </span>
              <div>
                <a className="button button-small" href={link({ page: page - 1 })} aria-label="Previous page of templates" aria-disabled={page <= 1 || undefined} tabIndex={page <= 1 ? -1 : undefined}>
                  Previous
                </a>
                <a className="button button-small" href={link({ page: page + 1 })} aria-label="Next page of templates" aria-disabled={page >= pagesTotal || undefined} tabIndex={page >= pagesTotal ? -1 : undefined}>
                  Next
                </a>
              </div>
            </div>
          </>
        )}
      </div>
      <p className="small muted">
        Every template here was set up in this studio. The sponsor account is the one typed at setup; it is never
        inferred from a title or file name.
      </p>
    </main>
  );
}
