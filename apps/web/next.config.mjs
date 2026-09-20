/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // @astra/contracts ships TypeScript source generated from the OpenAPI schema.
  transpilePackages: ["@astra/contracts"],
  eslint: { ignoreDuringBuilds: false },
  typescript: { ignoreBuildErrors: false },
};

export default nextConfig;
