// What the page needs from the studio's FastAPI app, fetched on this server. In local mode the API
// answers only loopback requests; hosted, it needs the viewer's session cookie, passed through.
import { headers } from "next/headers";

const API = process.env.STUDIO_API ?? "http://127.0.0.1:8000";

export type Summary = {
  id: string;
  revision: number;
  field_count: number;
  status: string; // lifecycle label, computed by the API
  saved_at?: string;
  countersigner?: boolean | null; // absent when the template cannot be read
  details?: { title?: string; sponsor_name?: string; sponsor_account_id?: string } | null;
  review?: { by: string } | null;
  approval?: { by: string } | null;
  error?: string | null;
};

async function get(path: string): Promise<Response> {
  const cookie = (await headers()).get("cookie");
  return fetch(`${API}${path}`, {
    cache: "no-store",
    headers: cookie ? { cookie } : {},
  });
}

// Both return a fallback, not an error, when the API is down or answers with something else, so the
// page can say so instead of failing.
export async function summaries(): Promise<Summary[] | null> {
  try {
    const response = await get("/api/templates");
    if (!response.ok) return null;
    return ((await response.json()) as { templates: Summary[] }).templates;
  } catch {
    return null;
  }
}

export async function stateMode(): Promise<string> {
  try {
    const response = await get("/healthz");
    return response.ok ? ((await response.json()) as { state: string }).state : "unknown";
  } catch {
    return "unknown";
  }
}
