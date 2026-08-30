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
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
