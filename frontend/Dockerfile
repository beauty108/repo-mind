FROM node:20-alpine AS builder

WORKDIR /app
COPY package*.json ./
RUN npm ci

COPY . .
# Set the API base URL for the production build
# When deployed locally via docker-compose, the backend is exposed on 8000
# and the frontend is accessed from the host browser.
# So the API URL must be the public localhost address, not the internal docker network address.
ENV VITE_API_BASE_URL=http://localhost:8000
RUN npm run build

FROM nginx:alpine
# Copy the built assets to Nginx
COPY --from=builder /app/dist /usr/share/nginx/html

# Add a simple nginx config for React Router / SPA routing
RUN echo 'server { \
    listen 80; \
    location / { \
        root /usr/share/nginx/html; \
        index index.html index.htm; \
        try_files $uri $uri/ /index.html; \
    } \
}' > /etc/nginx/conf.d/default.conf

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
