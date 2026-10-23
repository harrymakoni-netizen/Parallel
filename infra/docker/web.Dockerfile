# syntax=docker/dockerfile:1.7
# Parallel browser application: a static build served by nginx, which also forwards /api to the API.
# The browser application can equally be hosted on any static host in front of the API.
#
#   docker build -f infra/docker/web.Dockerfile -t parallel-web .
ARG NODE_IMAGE=node:22-bookworm-slim
ARG NGINX_IMAGE=nginx:1.27-alpine
FROM ${NODE_IMAGE} AS build
WORKDIR /web
COPY infra/docker/build-ca.sh /usr/local/bin/build-ca
COPY apps/web/package.json apps/web/package-lock.json ./
RUN --mount=type=secret,id=ca,required=false --mount=type=cache,target=/root/.npm \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 build-ca npm ci --no-audit --no-fund
COPY apps/web/ ./
# served files must be readable by nginx whatever modes the build context had
RUN npm run build && chmod -R a+rX dist

FROM ${NGINX_IMAGE}
COPY infra/docker/web/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY infra/docker/web/security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY --from=build /web/dist /usr/share/nginx/html
# The API address inside the deployment network
ENV PARALLEL_API_UPSTREAM=api:8000
EXPOSE 8080
