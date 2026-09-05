import type { NextConfig } from "next";

// The FastAPI app mounts no CORS middleware (backend/app/api/main.py), so a
// browser on :3000 cannot fetch :8000 directly. Proxying /api/v1 through Next
// keeps every request same-origin, which is why NEXT_PUBLIC_API_BASE_URL
// defaults to the relative "/api/v1". Override the upstream with
// API_PROXY_TARGET (server-side only — deliberately not NEXT_PUBLIC_, so it is
// never inlined into the client bundle). Harmless with no backend running: the
// app defaults to MockApi and never hits these paths.
const API_PROXY_TARGET = process.env.API_PROXY_TARGET ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  // Emits .next/standalone: server.js plus only the modules it actually
  // imports, so the Cloud Run image carries no node_modules and no npm.
  output: "standalone",
  // /scenes/:id and /timeline were two halves of one job — a shot list here, a
  // timeline there, no shared scene. They are now one route. 307 (permanent:
  // false) rather than 308: this is a product layout decision, and a 308 is
  // cached by browsers forever.
  async redirects() {
    return [
      { source: "/timeline", destination: "/workspace", permanent: false },
      {
        source: "/scenes/:sceneId",
        destination: "/workspace?scene=:sceneId",
        permanent: false,
      },
    ];
  },
  async rewrites() {
    return [
      {
        source: "/api/v1/:path*",
        destination: `${API_PROXY_TARGET}/api/v1/:path*`,
      },
    ];
  },
};

export default nextConfig;
