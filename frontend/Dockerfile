# syntax=docker/dockerfile:1
# Production frontend image: static build served by unprivileged Nginx.
# Build context: repository root (the build reads data/maps and protocol/).
#   docker build -f frontend/Dockerfile -t nether-earth-frontend:<version> .

# Digest-pinned (NE-09); Dependabot (docker ecosystem) proposes updates.
FROM node:22-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 AS build
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY data/maps /src/data/maps
COPY protocol /src/protocol
COPY frontend ./
# Which commit this bundle was built from; Vite inlines VITE_* at build time
# and main.ts logs it, so a loaded page reports its own build.
ARG GIT_COMMIT=unknown
ENV VITE_GIT_COMMIT=$GIT_COMMIT
RUN npm run build

# Digest-pinned (NE-09); Dependabot (docker ecosystem) proposes updates.
FROM nginxinc/nginx-unprivileged:1.28-alpine@sha256:7377697a821c131a924a7105fafbe7414db4e9fcc77a6f08f776f33f141ec3f8 AS runtime
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/frontend/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD ["wget", "-q", "--spider", "http://127.0.0.1:8080/"]
