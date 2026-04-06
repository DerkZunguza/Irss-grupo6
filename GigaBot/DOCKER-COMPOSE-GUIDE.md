# 📘 Guia Definitivo: Docker Compose para Dev e Deploy

## 🎯 Objetivo

Este documento explica como configurar Docker Compose para funcionar tanto em **desenvolvimento local** quanto em **produção (deploy via Portainer + Traefik)**.

---

## 📁 Estrutura de Arquivos

```
projeto/
├── docker-compose.yml          # Produção (Portainer + Traefik)
├── docker-compose.dev.yml      # Desenvolvimento local
├── .env                        # NÃO commitar (local)
├── .env.example                # Template para .env
├── backend/
│   ├── Dockerfile
│   └── src/
└── frontend/
    ├── Dockerfile
    └── src/
```

---

## 🔧 1. Docker Compose para DESENVOLVIMENTO (docker-compose.dev.yml)

### Características:
- Expõe portas publicamente (localhost)
- Não usa Traefik
- Volumes para hot-reload (opcional)
- Fácil debugging

### Template:

```yaml
services:
  mysql:
    image: mysql:8
    container_name: projeto-mysql-dev
    restart: unless-stopped
    ports:
      - "3307:3306"  # Porta diferente para não conflitar com MySQL local
    environment:
      MYSQL_ROOT_PASSWORD: ${MYSQL_ROOT_PASSWORD}
      MYSQL_DATABASE: nome_database
      MYSQL_USER: usuario
      MYSQL_PASSWORD: ${MYSQL_PASSWORD}
    volumes:
      - mysql-data-dev:/var/lib/mysql
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "localhost"]
      interval: 10s
      timeout: 5s
      retries: 5

  api:
    build: ./backend
    container_name: projeto-api-dev
    restart: unless-stopped
    ports:
      - "3000:3000"  # Expõe porta para acessar localhost:3000
    environment:
      NODE_ENV: development
      DB_HOST: mysql
      DB_USER: usuario
      DB_PASSWORD: ${MYSQL_PASSWORD}
      DB_NAME: nome_database
      JWT_SECRET: ${JWT_SECRET}
      PORT: 3000
    depends_on:
      mysql:
        condition: service_healthy
    # Opcional: hot-reload (descomentar se necessário)
    # volumes:
    #   - ./backend/src:/app/src

  frontend:
    build: ./frontend
    container_name: projeto-frontend-dev
    restart: unless-stopped
    ports:
      - "80:80"  # Acessa via localhost
    depends_on:
      - api
    # Opcional: hot-reload para desenvolvimento
    # volumes:
    #   - ./frontend/src:/app/src

volumes:
  mysql-data-dev:
```

### Como usar:

```bash
# Subir
docker compose -f docker-compose.dev.yml up -d --build

# Ver logs
docker compose -f docker-compose.dev.yml logs -f

# Parar
docker compose -f docker-compose.dev.yml down

# Parar e remover volumes (limpar banco)
docker compose -f docker-compose.dev.yml down -v
```

---

## 🚀 2. Docker Compose para PRODUÇÃO (docker-compose.yml)

### Características:
- **NÃO expõe portas** (exceto frontend via Traefik)
- Usa **network externa** (traefik-public)
- Labels do Traefik para SSL automático
- Restart automático

### Template:

```yaml
services:
  mysql:
    image: mysql:8
    container_name: projeto-mysql
    restart: unless-stopped
    networks:
      - traefik-public  # Network do Traefik
    # NÃO coloque "ports:" aqui! MySQL fica interno
    environment:
      MYSQL_ROOT_PASSWORD: ${MYSQL_ROOT_PASSWORD}
      MYSQL_DATABASE: nome_database
      MYSQL_USER: usuario
      MYSQL_PASSWORD: ${MYSQL_PASSWORD}
    volumes:
      - mysql-data:/var/lib/mysql
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "localhost"]
      interval: 10s
      timeout: 5s
      retries: 5

  api:
    build: ./backend
    container_name: projeto-api
    restart: unless-stopped
    networks:
      - traefik-public
    # NÃO coloque "ports:" aqui! API fica interna
    environment:
      NODE_ENV: production
      DB_HOST: mysql  # Nome do serviço (Docker DNS)
      DB_USER: usuario
      DB_PASSWORD: ${MYSQL_PASSWORD}
      DB_NAME: nome_database
      JWT_SECRET: ${JWT_SECRET}
      PORT: 3000
    depends_on:
      mysql:
        condition: service_healthy

  frontend:
    build: ./frontend
    container_name: projeto-frontend
    restart: unless-stopped
    networks:
      - traefik-public
    # NÃO coloque "ports:" aqui! Traefik gerencia isso
    depends_on:
      - api
    labels:
      # TRAEFIK LABELS - ATENÇÃO AO DOMÍNIO!
      - "traefik.enable=true"
      - "traefik.http.routers.projeto.rule=Host(`seudominio.duckdns.org`)"
      - "traefik.http.routers.projeto.entrypoints=websecure"
      - "traefik.http.routers.projeto.tls.certresolver=letsencrypt"
      - "traefik.http.services.projeto.loadbalancer.server.port=80"

volumes:
  mysql-data:

networks:
  traefik-public:
    external: true  # Network criada externamente
```

### Pontos CRÍTICOS de Atenção:

#### ❌ ERROS COMUNS:

1. **Expor portas em produção:**
```yaml
# ERRADO - NÃO FAÇA ISSO EM PRODUÇÃO
ports:
  - "3000:3000"
```

2. **Nome errado do upstream no Nginx:**
```dockerfile
# ERRADO - "cofrekeys-api-dev" é nome do container
proxy_pass http://cofrekeys-api-dev:3000;

# CERTO - "api" é o nome do serviço
proxy_pass http://api:3000;
```

3. **Network não externa:**
```yaml
# ERRADO
networks:
  traefik-public:
    driver: bridge

# CERTO
networks:
  traefik-public:
    external: true
```

4. **Esquecer de criar network do Traefik:**
```bash
# Execute isso na VPS ANTES do deploy
docker network create traefik-public
```

---

## 🔌 3. Dockerfile do Frontend (CRÍTICO!)

### Para funcionar com Nginx + API proxy:

```dockerfile
FROM node:18-alpine AS builder

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .
RUN npm run build

FROM nginx:alpine

COPY --from=builder /app/dist /usr/share/nginx/html

# ATENÇÃO: Use o nome do SERVIÇO, não do container!
RUN echo 'server { \
    listen 80; \
    root /usr/share/nginx/html; \
    index index.html; \
    location / { \
        try_files $uri $uri/ /index.html; \
    } \
    location /api { \
        proxy_pass http://api:3000; \
        proxy_http_version 1.1; \
        proxy_set_header Host $host; \
        proxy_set_header X-Real-IP $remote_addr; \
    } \
}' > /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
```

### ⚠️ ATENÇÃO: Nome do upstream

- **DEV:** `http://api:3000` ✅
- **PROD:** `http://api:3000` ✅
- **ERRADO:** `http://cofrekeys-api:3000` ❌
- **ERRADO:** `http://localhost:3000` ❌

**Sempre use o nome do SERVIÇO definido no docker-compose!**

---

## 📝 4. Variáveis de Ambiente

### .env.example (comitar no Git):

```env
MYSQL_ROOT_PASSWORD=root_password_123
MYSQL_PASSWORD=password_123
JWT_SECRET=my_super_secret_jwt_key_change_this
```

### .env (NÃO comitar - apenas local):

```env
MYSQL_ROOT_PASSWORD=SenhaLocalDev123!
MYSQL_PASSWORD=SenhaLocalDev456!
JWT_SECRET=jwt_local_dev_secret_789
```

### No Portainer (Produção):

Configurar **Environment Variables** ao criar a Stack:

```
MYSQL_ROOT_PASSWORD=SenhaProdSegura2024!@#
MYSQL_PASSWORD=SenhaProdMySQL2024!@#
JWT_SECRET=jwt_prod_super_secret_randomstring_12345
```

**IMPORTANTE:** NUNCA use as mesmas senhas em dev e produção!

---

## 🔄 5. Fluxo de Deploy Completo

### Local (Desenvolvimento):

```bash
# 1. Clone o repo
git clone https://github.com/usuario/projeto
cd projeto

# 2. Configure .env
cp .env.example .env
# Edite o .env com suas senhas locais

# 3. Suba
docker compose -f docker-compose.dev.yml up -d --build

# 4. Acesse
http://localhost
```

### VPS (Produção via Portainer):

```bash
# 1. Crie a network (uma vez só)
docker network create traefik-public

# 2. No Portainer:
# - Stacks > Add Stack
# - Name: projeto
# - Build method: Repository
# - Repository URL: https://github.com/usuario/projeto
# - Repository reference: refs/heads/main
# - Compose path: docker-compose.yml
# - Environment variables: (adicionar MYSQL_ROOT_PASSWORD, MYSQL_PASSWORD, JWT_SECRET)
# - Deploy!

# 3. Aguarde o build

# 4. Verifique os containers
docker ps | grep projeto

# 5. Acesse
https://seudominio.duckdns.org
```

---

## ✅ Checklist de Configuração

### Antes de fazer deploy:

- [ ] docker-compose.yml SEM "ports" (exceto se realmente necessário)
- [ ] docker-compose.yml usa "networks: traefik-public"
- [ ] Labels do Traefik corretos (domínio, entrypoint, tls)
- [ ] Dockerfile do frontend usa nome do SERVIÇO para proxy_pass
- [ ] .env NÃO está no Git (.gitignore configurado)
- [ ] .env.example está no Git
- [ ] Network "traefik-public" criada na VPS
- [ ] Traefik rodando na VPS
- [ ] DuckDNS apontando para IP da VPS
- [ ] Variáveis de ambiente configuradas no Portainer
- [ ] README.md com instruções de uso

---

## 🐛 Troubleshooting

### "Network traefik-public not found"
```bash
docker network create traefik-public
```

### "host not found in upstream api"
- Verifique o nome no proxy_pass do Dockerfile
- Deve ser o nome do SERVIÇO (api), não do container (projeto-api)

### "Connection refused" ao acessar API
- Verifique se API está rodando: `docker logs projeto-api`
- Verifique se MySQL está healthy: `docker ps`
- Verifique variáveis de ambiente

### Site não carrega (504 Gateway Timeout)
- Verifique logs do Traefik
- Verifique se domínio aponta para IP correto: `nslookup seudominio.duckdns.org`
- Verifique labels do Traefik no docker-compose

### Mudanças não aparecem após rebuild
```bash
# Força rebuild sem cache
docker compose -f docker-compose.yml build --no-cache
docker compose -f docker-compose.yml up -d
```

---

## 📚 Resumo das Diferenças

| Aspecto | Desenvolvimento | Produção |
|---------|----------------|----------|
| **Arquivo** | docker-compose.dev.yml | docker-compose.yml |
| **Portas** | Expostas (80, 3000, 3306) | NÃO expostas |
| **Network** | Default (bridge) | traefik-public (external) |
| **SSL** | Não | Sim (via Traefik) |
| **Domínio** | localhost | seudominio.duckdns.org |
| **Restart** | no ou unless-stopped | unless-stopped |
| **Labels Traefik** | Não | Sim |
| **Volumes** | Pode ter hot-reload | Apenas dados persistentes |
| **NODE_ENV** | development | production |

---

## 🎓 Lições Aprendidas

### 1. Nome do upstream é SERVIÇO, não CONTAINER
```yaml
services:
  api:  # ← Este é o nome para usar no proxy_pass
    container_name: projeto-api  # ← Este NÃO é o nome para usar
```

### 2. Produção NÃO expõe portas (Traefik gerencia)
```yaml
# DEV: expõe porta
ports:
  - "3000:3000"

# PROD: sem ports
# Traefik acessa via network interna
```

### 3. MySQL interno em produção
```yaml
# MySQL em produção NÃO precisa expor porta
# Apenas API se conecta via Docker DNS
DB_HOST: mysql  # Nome do serviço
```

### 4. Sempre use variáveis de ambiente
```yaml
# NÃO faça isso:
MYSQL_PASSWORD: senha123

# Faça isso:
MYSQL_PASSWORD: ${MYSQL_PASSWORD}
```

---

## 🎯 Template Rápido para Novos Projetos

```bash
# Copiar estrutura
cp docker-compose.yml novo-projeto/
cp docker-compose.dev.yml novo-projeto/
cp .env.example novo-projeto/

# Ajustar:
# 1. Nomes dos containers
# 2. Domínio no Traefik
# 3. Nome do banco de dados
# 4. Porta da API (se diferente de 3000)
# 5. Nome do upstream no Dockerfile do frontend
```

---

**Testado e funcionando em produção! ✅**

Qualquer dúvida, consulte este documento antes de fazer deploy.

---

**Autor:** Claude Code
**Projeto de exemplo:** CofreKeys
**Data:** 2025-11-07
