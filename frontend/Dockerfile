FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
# npm only links ``@ws/*`` workspace packages whose
# directories exist when the install runs, so every
# workspace package must be copied before ``npm ci``.
# Installing first (as a plain single-package project
# would) leaves node_modules/@ws empty and the build
# fails with "Cannot find module '@ws/types'".
COPY packages/ packages/
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD wget -qO- http://127.0.0.1:80/ || exit 1
CMD ["nginx", "-g", "daemon off;"]
