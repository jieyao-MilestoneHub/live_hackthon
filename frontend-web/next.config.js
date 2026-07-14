/** @type {import('next').NextConfig} */
// Static export target: S3 + CloudFront. No SSR / no API routes at runtime.
const nextConfig = {
  output: 'export',
  images: {
    // Required for `output: 'export'` — no on-the-fly image optimization server.
    unoptimized: true,
  },
  // Emit /jobs/index.html (not /jobs.html) so S3/CloudFront static hosting
  // resolves the route cleanly with a directory-index default.
  trailingSlash: true,
  reactStrictMode: true,
};

module.exports = nextConfig;
