import { NextResponse, type NextRequest } from "next/server";

// Today's pages send `script-src 'self'` with no inline script at all. The App Router streams its
// payload in inline <script> tags, so the nearest equivalent is a fresh nonce per request: Next
// reads it from the request's CSP header and puts it on its own scripts. SPIKE_CSP=classic sends
// today's policy unchanged, to show what it blocks.
export function proxy(request: NextRequest) {
  const nonce = btoa(crypto.randomUUID());
  const script =
    process.env.SPIKE_CSP === "classic" ? "script-src 'self'" : `script-src 'self' 'nonce-${nonce}'`;
  const policy = [
    "default-src 'none'",
    script,
    "style-src 'self'",
    "img-src 'self' data:",
    "connect-src 'self'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join("; ");
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
  matcher: [{ source: "/((?!api/|static/|_next/static|favicon.ico).*)" }],
};
