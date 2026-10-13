This is a [Next.js](https://nextjs.org) project bootstrapped with [`create-next-app`](https://nextjs.org/docs/app/api-reference/cli/create-next-app).

## Getting Started

First, run the development server:

```bash
npm run dev
# or
yarn dev
# or
pnpm dev
# or
bun dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser to see the result.

You can start editing the page by modifying `app/page.tsx`. The page auto-updates as you edit the file.

This project uses [`next/font`](https://nextjs.org/docs/app/building-your-application/optimizing/fonts) to automatically optimize and load [Geist](https://vercel.com/font), a new font family for Vercel.

## Learn More

To learn more about Next.js, take a look at the following resources:

- [Next.js Documentation](https://nextjs.org/docs) - learn about Next.js features and API.
- [Learn Next.js](https://nextjs.org/learn) - an interactive Next.js tutorial.

You can check out [the Next.js GitHub repository](https://github.com/vercel/next.js) - your feedback and contributions are welcome!

## Deploy on Vercel

The easiest way to deploy your Next.js app is to use the [Vercel Platform](https://vercel.com/new?utm_medium=default-template&filter=next.js&utm_source=create-next-app&utm_campaign=create-next-app-readme) from the creators of Next.js.

Check out our [Next.js deployment documentation](https://nextjs.org/docs/app/building-your-application/deploying) for more details.

## Runtime browser endpoints and reverse proxies

The standalone image reads `NEXT_PUBLIC_LANGGRAPH_API_URL` and
`NEXT_PUBLIC_TERMINAL_WS_URL` when serving the dashboard. Set them on the
running web container; a rebuild is not required. For a same-origin reverse
proxy, for example:

```env
NEXT_PUBLIC_LANGGRAPH_API_URL=/langgraph
NEXT_PUBLIC_TERMINAL_WS_URL=/terminal
```

The root layout dynamically supplies only these two public values before the
browser creates its LangGraph client or terminal connection. Reload the page
after changing container configuration. Unset values keep the local defaults
`http://localhost:2024` and `ws://localhost:3003`.

Relative paths resolve against the dashboard origin, including its port.
Use a query/fragment-free base path for the LangGraph API; the SDK appends
its own request paths and query parameters.
An HTTPS dashboard uses `wss:` for a relative terminal path. Terminal endpoint
query parameters are retained when engagement metadata is added. Settings
shows the runtime browser API endpoint.

These settings do not create proxy routes or add authentication. Configure
and authenticate the reverse proxy yourself, including WebSocket upgrades.
Do not publish raw unauthenticated LangGraph/terminal ports. The existing CSP
is unchanged; arbitrary cross-origin endpoints are still subject to it.
These two variables are public: never put credentials in their values.

Runtime regression check (after the normal production build):

```bash
node --import tsx tests/web/runtime-config.mjs
```

Run that command from the repository root. It reuses the same production build
with two runtime configurations and the default configuration, checks the
serialized configuration/cache headers, and exercises SDK requests and
WebSocket upgrades against a local stand-in. It does not run a real browser,
LangGraph backend, or terminal PTY.
