/** @type {import('next').NextConfig} */
const nextConfig = {
  // TS route handlers under app/api/* are the control plane. Python deterministic
  // tools live under top-level api/*.py (Vercel Functions), outside Next's routing.
};

export default nextConfig;
