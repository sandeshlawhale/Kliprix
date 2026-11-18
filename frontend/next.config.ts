import type { NextConfig } from "next";

// The browser talks to one origin; Next.js proxies /api/* to the FastAPI backend
// (docs/06-architecture.md, boundary rule 3).
const backendUrl = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  experimental: {
    // The default proxy timeout (30 s) cut off slow backend calls such as "Test connection"
    // or "Update downloader" ("socket hang up"). The backend has its own timeouts.
    proxyTimeout: 600_000,
  },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backendUrl}/api/:path*` }];
  },
};

export default nextConfig;
