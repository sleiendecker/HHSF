# The map, built once and served by Caddy with /tiles/ proxied to the tile server
# (docker-compose.yml, service `web`; `make serve`). Development uses `make web` instead.
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM caddy:2.10-alpine
COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist /srv
