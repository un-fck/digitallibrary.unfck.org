import type { NextConfig } from 'next'

const nextConfig: NextConfig = {
    reactStrictMode: true,
    poweredByHeader: false,
    async rewrites() {
        return [
            {
                source: '/v1/:path*',
                destination: 'http://localhost:8000/v1/:path*',
            },
        ]
    },
}

export default nextConfig
