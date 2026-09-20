# ASTRA command centre.
FROM node:20-alpine AS deps
WORKDIR /repo
COPY package.json package-lock.json ./
COPY apps/web/package.json apps/web/package.json
COPY packages/contracts/package.json packages/contracts/package.json
RUN npm ci

FROM node:20-alpine AS build
WORKDIR /repo
COPY --from=deps /repo/node_modules ./node_modules
COPY --from=deps /repo/apps/web/node_modules ./apps/web/node_modules
COPY package.json package-lock.json ./
COPY packages ./packages
COPY apps/web ./apps/web
# Baked into the client bundle at build time; override per deployment.
ARG NEXT_PUBLIC_ASTRA_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_ASTRA_API_URL=${NEXT_PUBLIC_ASTRA_API_URL}
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build --workspace @astra/web

FROM node:20-alpine AS runtime
WORKDIR /repo
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=build /repo/node_modules ./node_modules
COPY --from=build /repo/apps/web ./apps/web
COPY --from=build /repo/packages ./packages
COPY --from=build /repo/package.json ./package.json
EXPOSE 3000
CMD ["sh", "-c", "npx next start apps/web -p ${PORT:-3000}"]
