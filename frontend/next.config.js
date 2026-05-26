/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: 'https://invoiceflow-backend-481y.onrender.com/:path*',
      },
    ];
  },
};

module.exports = nextConfig;
