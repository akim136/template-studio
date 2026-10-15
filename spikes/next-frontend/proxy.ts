import { NextResponse, type NextRequest } from "next/server";

// Local mode only, like the spike. FastAPI answers loopback callers, and every request Next sends
// it comes from loopback, so this server applies the same rule first (studio/state/api.py,
// _loopback): a loopback Host, and an Origin, if any, on a loopback host. The npm scripts also
// bind to 127.0.0.1.
const LOOPBACK = new Set(["127.0.0.1", "localhost", "[::1]"]);

function local(request: NextRequest): boolean {
  try {
    const host = new URL(`http://${request.headers.get("host") ?? ""}`).hostname;
    if (!LOOPBACK.has(host)) return false;
    const origin = request.headers.get("origin");
    if (origin === null) return true;
    const page = new URL(origin);
    return ["http:", "https:"].includes(page.protocol) && LOOPBACK.has(page.hostname);
  } catch {
    return false; // a malformed Host or Origin
  }
}

// Today's pages send `script-src 'self'` with no inline script at all. The App Router streams its
// payload in inline <script> tags, so the nearest equivalent is a fresh nonce per request: Next
// reads it from the request's CSP header and puts it on its own scripts. SPIKE_CSP=classic sends
// today's policy unchanged, to show what it blocks.
function pagePolicy(nonce: string): string {
  const script =
    process.env.SPIKE_CSP === "classic" ? "script-src 'self'" : `script-src 'self' 'nonce-${nonce}'`;
  return [
    "default-src 'none'",
    script,
    "style-src 'self'",
    "img-src 'self' data:",
    "connect-src 'self'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join("; ");
}

export function proxy(request: NextRequest) {
  if (!local(request)) {
    const detail = "saved state answers only loopback requests from loopback pages";
    return NextResponse.json({ detail }, { status: 403 });
  }
  const path = request.nextUrl.pathname;
  if (path.startsWith("/api/") || path.startsWith("/static/") || path === "/favicon.ico") {
    return NextResponse.next(); // proxied to FastAPI, which sets its own headers
  }
  const policy = pagePolicy(btoa(crypto.randomUUID()));
  const headers = new Headers(request.headers);
  headers.set("content-security-policy", policy);
  const response = NextResponse.next({ request: { headers } });
  response.headers.set("content-security-policy", policy);
  response.headers.set("x-content-type-options", "nosniff");
  response.headers.set("referrer-policy", "same-origin");
  response.headers.set("cache-control", "no-store");
  return response;
}

export const config = {
  matcher: [{ source: "/((?!_next/static|_next/image).*)" }],
};
