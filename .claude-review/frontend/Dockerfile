# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------
# Frontend image — build the Vite bundle, then serve it as static files
# behind nginx with a WebSocket proxy to the backend.
# ---------------------------------------------------------------------------
FROM node:20-alpine AS builder
WORKDIR /app
COPY package.json ./
# package-lock.json is optional — copy if present, install accordingly.
COPY package-lock.jso[n] ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi
COPY . .
RUN npm run build

FROM nginx:1.27-alpine AS runtime
# Keep static assets read-only so the non-root nginx user cannot modify them.
COPY --from=builder --chown=root:root --chmod=755 /app/dist /usr/share/nginx/html
RUN find /usr/share/nginx/html -type d -exec chmod 755 {} + \
    && find /usr/share/nginx/html -type f -exec chmod 644 {} +
COPY nginx.conf /etc/nginx/conf.d/default.conf
USER nginx
EXPOSE 80
