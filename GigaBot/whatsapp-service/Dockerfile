# Dockerfile — WhatsApp Service (Baileys)
FROM node:20-alpine

# Dependências nativas para criptografia do Baileys
RUN apk add --no-cache python3 make g++ libc6-compat

WORKDIR /app

COPY package*.json ./
RUN npm install --omit=dev

COPY src/ ./src/

# Volume para persistir auth state das sessões (evitar re-scan de QR)
VOLUME ["/app/sessions-data"]

EXPOSE 3001

CMD ["node", "src/index.js"]
