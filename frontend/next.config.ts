import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // UAT live 套件用 127.0.0.1:3001 连 Next dev;Next 16 默认只允许 localhost 访问
  // dev 资源(HMR/client bundle),否则页面不 hydrate。放开本地回环。
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://127.0.0.1:8000/api/:path*",
      },
    ];
  },
};

export default nextConfig;
