# Build from the repository root: docker build -f frontend/Dockerfile .
# Stage 1 installs exactly what package-lock.json records.
FROM node:26.8.2-bookworm-slim@sha256:cd9f682fa2885cd1056e830424764158570061c59736a1da836bc3d73df095ae AS dependencies
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# Development runs Vite (the editor-friendly web server).
FROM dependencies AS development
COPY frontend/ ./
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173", "--strictPort"]

# Browser tests need Chromium and Linux browser libraries. They stay out of the
# final website image. npx uses the Playwright version already installed above.
FROM development AS test
ENV CI=true
RUN npx playwright install --with-deps chromium
CMD ["npm", "run", "test"]

# Build turns React/TypeScript source into files a browser can read.
FROM development AS build
RUN npm run build

# Caddy serves those files and forwards /api requests to the Python service.
# Node, source code and test browsers are not needed in this final stage.
FROM caddy:2.11.4-alpine@sha256:6aeddd44c3078b0f9a35206472a11420648a79c184603ef95957d0a20044cb2b AS runtime
COPY frontend/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist /srv
EXPOSE 8080
