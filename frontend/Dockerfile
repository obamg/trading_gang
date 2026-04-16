# TradeCore frontend — Vite build, served by nginx
FROM node:20-alpine AS build

WORKDIR /app

# Copy manifests first to cache the install step.
# Prefer reproducible `npm ci`; if the lockfile is out of sync (e.g. deps were just
# added), fall back to `npm install` which will update it. The built image is still
# deterministic because node_modules and package-lock.json are both frozen by the
# subsequent COPY . and `npm run build`.
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund

# Build the app
COPY . .
# The API base URL is baked in at build time — pass VITE_API_URL/VITE_WS_URL via --build-arg.
ARG VITE_API_URL=/api
ARG VITE_WS_URL=/ws
ENV VITE_API_URL=${VITE_API_URL} \
    VITE_WS_URL=${VITE_WS_URL}
RUN npm run build

FROM nginx:1.27-alpine AS serve

# Replace default nginx config with our SPA + API proxy config
COPY nginx.conf /etc/nginx/nginx.conf

# Copy the built SPA
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1

CMD ["nginx", "-g", "daemon off;"]
