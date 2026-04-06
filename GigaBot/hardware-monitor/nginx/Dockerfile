# nginx/Dockerfile — Nginx com watcher de IPs banidos
FROM nginx:alpine

# Copiar script watcher para o directório de entrypoint do nginx
# Todos os scripts em /docker-entrypoint.d/ são executados no arranque
COPY watcher.sh /docker-entrypoint.d/40-watch-blocked-ips.sh
RUN chmod +x /docker-entrypoint.d/40-watch-blocked-ips.sh
