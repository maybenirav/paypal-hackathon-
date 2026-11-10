/** @type {import('next').NextConfig} */
const nextConfig = {
  // Self-contained server in .next/standalone: the Docker image ships only what it needs.
  output: "standalone",
};

export default nextConfig;
