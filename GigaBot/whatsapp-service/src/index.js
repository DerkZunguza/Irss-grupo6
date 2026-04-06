// index.js — Ponto de entrada do serviço WhatsApp
'use strict';

require('dotenv').config();
const express      = require('express');
const path         = require('path');
const logger       = require('./utils/logger');
const { SessionManager } = require('./sessions/SessionManager');
const MessageQueue = require('./queue/MessageQueue');
const createRouter = require('./api/routes');
const axios        = require('axios');

const PORT        = parseInt(process.env.PORT        || '3001');
const POOL_SIZE   = parseInt(process.env.POOL_SIZE   || '2');
const REDIS_HOST  = process.env.REDIS_HOST  || 'redis-wa';
const REDIS_PORT  = parseInt(process.env.REDIS_PORT  || '6379');
const WEBHOOK_URL = process.env.WEBHOOK_URL || null;
const SESSIONS_DIR = path.join(process.cwd(), 'sessions-data');

// ── Handler de mensagens recebidas — envia para webhook se configurado ────────
async function onMessageReceived(payload) {
  logger.info({ from: payload.from, body: payload.body.substring(0, 80) }, 'Mensagem recebida');

  if (!WEBHOOK_URL) return;

  try {
    await axios.post(WEBHOOK_URL, payload, {
      headers: { 'Content-Type': 'application/json' },
      timeout: 5000,
    });
    logger.debug({ webhookUrl: WEBHOOK_URL }, 'Webhook entregue com sucesso');
  } catch (err) {
    logger.warn({ err: err.message }, 'Falha ao entregar webhook — continuando...');
  }
}

async function main() {
  logger.info('═══════════════════════════════════════════');
  logger.info(' WhatsApp Service — GigaBot               ');
  logger.info(`  Pool size  : ${POOL_SIZE} sessão(ões)`);
  logger.info(`  Porta      : ${PORT}`);
  logger.info(`  Redis      : ${REDIS_HOST}:${REDIS_PORT}`);
  logger.info(`  Webhook    : ${WEBHOOK_URL || 'não configurado'}`);
  logger.info('═══════════════════════════════════════════');

  // ── Inicializar pool de sessões ──────────────────────────────────────────
  const sessionManager = new SessionManager({
    poolSize:    POOL_SIZE,
    sessionsDir: SESSIONS_DIR,
    onMessage:   onMessageReceived,
  });

  await sessionManager.init();

  // ── Inicializar fila de mensagens ─────────────────────────────────────────
  const messageQueue = new MessageQueue({
    redisHost:      REDIS_HOST,
    redisPort:      REDIS_PORT,
    sessionManager,
  });

  // ── API Express ───────────────────────────────────────────────────────────
  const app = express();
  app.use(express.json());

  // Rota de saúde sem autenticação
  app.get('/health', (req, res) => {
    const status  = sessionManager.getStatus();
    const healthy = status.connected > 0;
    res.status(healthy ? 200 : 503).json({ success: healthy, data: status });
  });

  // Todas as outras rotas requerem token
  app.use('/api', createRouter(sessionManager, messageQueue));

  app.listen(PORT, () => {
    logger.info(`[API] Servidor iniciado em http://0.0.0.0:${PORT}`);
    logger.info(`[API] Ver QR da sessão 1: http://localhost:${PORT}/api/sessions/s1/qr?format=html`);
  });

  // ── Tratamento de erros não capturados ────────────────────────────────────
  process.on('unhandledRejection', (err) => {
    logger.error({ err }, 'Promessa rejeitada não tratada');
  });

  process.on('uncaughtException', (err) => {
    logger.error({ err }, 'Excepção não capturada — o processo vai continuar');
  });

  // Graceful shutdown
  process.on('SIGTERM', async () => {
    logger.info('SIGTERM recebido — encerrando graciosamente...');
    process.exit(0);
  });
}

main().catch(err => {
  logger.error({ err }, 'Erro fatal ao iniciar o serviço');
  process.exit(1);
});
