# ── Build stage ───────────────────────────────────────────────────────────────
FROM node:22-alpine AS builder

WORKDIR /app

# Install all dependencies (including devDeps needed for tsc)
COPY package.json package-lock.json ./
RUN npm ci

# Compile TypeScript
COPY tsconfig.json index.ts ./
RUN npm run build

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM node:22-alpine AS runtime

WORKDIR /app

# Install only production dependencies
COPY package.json package-lock.json ./
RUN npm ci --omit=dev

# Copy compiled output from builder
COPY --from=builder /app/dist ./dist

# Non-root user for security
RUN addgroup -S mcp && adduser -S mcp -G mcp
USER mcp

ENV MCP_HOST=0.0.0.0
ENV MCP_PORT=3333

EXPOSE 3333

CMD ["node", "dist/index.js"]
