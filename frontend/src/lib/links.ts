// Client-side link check before calling the backend (the backend validates again).

/** True when `raw` is an http(s) URL on one of `hosts` or their subdomains. */
export function isSupportedLink(raw: string, hosts: string[]): boolean {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return false;
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") return false;
  const host = url.hostname.toLowerCase();
  return hosts.some((allowed) => host === allowed || host.endsWith(`.${allowed}`));
}

/** The project's GitHub repository (Documentation, About page). */
export const REPO_URL = "https://github.com/sandeshlawhale/Kliprix";
