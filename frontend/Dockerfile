# Kliprix frontend (optional Docker path; docs/06 "Docker").
FROM node:22-bookworm-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# The /api proxy target is read when the app is built and when it starts.
ARG BACKEND_URL=http://backend:8000
ENV BACKEND_URL=$BACKEND_URL \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-bookworm-slim
WORKDIR /app
ARG BACKEND_URL=http://backend:8000
ENV NODE_ENV=production \
    BACKEND_URL=$BACKEND_URL \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=build /app ./
EXPOSE 3000
# 0.0.0.0 inside the container only; docker-compose publishes the port on 127.0.0.1.
CMD ["node", "scripts/next.mjs", "start", "--hostname", "0.0.0.0", "--port", "3000"]
