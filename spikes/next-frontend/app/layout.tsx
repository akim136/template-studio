import type { Metadata } from "next";
import type { ReactNode } from "react";
import { stateMode } from "./studio";

export const metadata: Metadata = {
  title: { template: "%s · Template Studio", default: "Template Studio" },
  icons: { icon: "/favicon.ico", apple: "/static/apple-touch-icon.png" },
};

export default async function RootLayout({ children }: { children: ReactNode }) {
  const local = (await stateMode()) === "local";
  return (
    <html lang="en">
      <head>
        {/* The studio's own stylesheet, proxied from FastAPI so both frontends share one file. */}
        <link rel="stylesheet" href="/static/studio.css" precedence="default" />
      </head>
      <body>
        <header className="app-header">
          <a className="brand" href="/" aria-label="Template Studio: all templates">
            <img className="brand-mark" src="/static/homebase-mark.png" width={51} height={24} alt="" />
            <span className="brand-divider" aria-hidden="true"></span>
            <span className="brand-name">Template Studio</span>
          </a>
          <span className="header-spacer"></span>
          {local ? (
            <span
              className="local-badge"
              title="Local mode answers only this machine. Nobody signs in, and sign-offs are recorded as anonymous."
            >
              Local · no sign-in
            </span>
          ) : null}
        </header>
        {children}
      </body>
    </html>
  );
}
