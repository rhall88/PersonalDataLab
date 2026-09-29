import { createSignal } from "solid-js";

// Hash routes so a direct link (or reload) restores the view: #/relationships/p.4?from=...
export type Route = { path: string[]; query: URLSearchParams };

function parse(): Route {
  const raw = location.hash.replace(/^#/, "") || "/ask";
  const [p, q] = raw.split("?");
  return { path: p.split("/").filter(Boolean).map(decodeURIComponent), query: new URLSearchParams(q ?? "") };
}

const [route, setRoute] = createSignal<Route>(parse());
window.addEventListener("hashchange", () => setRoute(parse()));
export { route };

export function href(path: string, query: Record<string, unknown> = {}): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return `#${path}${s ? `?${s}` : ""}`;
}

export function go(path: string, query: Record<string, unknown> = {}): void {
  location.hash = href(path, query).slice(1);
}
