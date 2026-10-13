# Web panel (Hono + Bun). Built by deploy/compose.yml; see web/README.md.
FROM oven/bun:1.3.12-slim

WORKDIR /app
COPY package.json bun.lock ./
RUN bun install --frozen-lockfile --production
COPY tsconfig.json ./
COPY src ./src
COPY public ./public

ENV NODE_ENV=production PORT=3100 BUN_RUNTIME_TRANSPILER_CACHE_PATH=0
USER bun
EXPOSE 3100
HEALTHCHECK --interval=60s --timeout=5s CMD ["bun", "-e", "fetch('http://127.0.0.1:3100/health').then(r => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"]
CMD ["bun", "src/index.tsx"]
