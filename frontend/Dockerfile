FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
# ``npm ci`` installs the exact tree in package-lock.json. It fails
# rather than silently resolving different versions, which is what
# ``npm install`` would do and what made builds unreproducible.
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD wget -qO- http://127.0.0.1:80/ || exit 1
CMD ["nginx", "-g", "daemon off;"]
