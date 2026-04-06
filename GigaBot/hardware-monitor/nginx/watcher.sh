#!/bin/sh
# watcher.sh — Detecta mudanças no blocked_ips.conf e recarrega nginx
# Colocado em /docker-entrypoint.d/ para executar no arranque do container

WATCHED="/etc/nginx/blocked_ips.conf"
LAST_HASH=""

echo "[watcher] A monitorar $WATCHED para mudanças..."

# Garantir que o ficheiro existe (pode ainda não ter sido gerado pela API)
if [ ! -f "$WATCHED" ]; then
    echo "[watcher] Ficheiro não existe ainda — criando vazio..."
    echo "# blocked_ips.conf — ainda sem IPs banidos" > "$WATCHED"
fi

# Executar em background para não bloquear o arranque do nginx
(
  while true; do
    if [ -f "$WATCHED" ]; then
      CURR=$(md5sum "$WATCHED" 2>/dev/null | cut -d' ' -f1)
      if [ "$CURR" != "$LAST_HASH" ]; then
        if [ -n "$LAST_HASH" ]; then
          echo "[watcher] blocked_ips.conf alterado — a recarregar nginx..."
          nginx -s reload
        fi
        LAST_HASH="$CURR"
      fi
    fi
    sleep 10
  done
) &
