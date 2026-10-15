import type { NextConfig } from "next";

// The studio's FastAPI app. The browser only ever talks to this Next server; the JSON API, the
// shared stylesheet and the icons are proxied, so the pages and the API stay one origin.
const API = process.env.STUDIO_API ?? "http://127.0.0.1:8000";

const config: NextConfig = {
  poweredByHeader: false,
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API}/api/:path*` },
      { source: "/static/:path*", destination: `${API}/static/:path*` },
      { source: "/favicon.ico", destination: `${API}/favicon.ico` },
    ];
  },
};

export default config;
