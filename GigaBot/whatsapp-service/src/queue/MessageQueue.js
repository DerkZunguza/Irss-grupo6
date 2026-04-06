// queue/MessageQueue.js — Fila de mensagens com Bull + Redis
// Garante que mensagens não se perdem quando a sessão cai
'use strict';

const Bull   = require('bull');
const logger = require('../utils/logger');

class MessageQueue {
  /**
   * @param {object} opts
   * @param {string} opts.redisHost
   * @param {number} opts.redisPort
   * @param {SessionManager} opts.sessionManager
   */
  constructor({ redisHost, redisPort, sessionManager }) {
    this.sessionManager = sessionManager;

    const redisConfig = { host: redisHost, port: redisPort };

    // Fila principal de saída
    this.outQueue = new Bull('whatsapp:outgoing', { redis: redisConfig });

    // Fila de retry para mensagens que falharam
    this.retryQueue = new Bull('whatsapp:retry', { redis: redisConfig });

    this._setupProcessors();
  }

  _setupProcessors() {
    // Processar fila de saída — 1 mensagem de cada vez por sessão
    this.outQueue.process(async (job) => {
      const { to, content, sessionId, attempt } = job.data;

      try {
        const result = await this.sessionManager.sendMessage(to, content, sessionId);
        logger.info({ to, result }, '[Queue] Mensagem enviada com sucesso');
        return result;
      } catch (err) {
        logger.warn({ err: err.message, to, attempt }, '[Queue] Falha ao enviar — vai para retry');
        throw err; // Bull fará retry automático conforme backoffStrategy
      }
    });

    // Configurar backoff exponencial: 5s, 10s, 20s, 40s, ...
    this.outQueue.on('failed', (job, err) => {
      logger.error({ jobId: job.id, err: err.message }, '[Queue] Job falhou definitivamente');
    });

    this.outQueue.on('completed', (job) => {
      logger.debug({ jobId: job.id }, '[Queue] Job concluído');
    });
  }

  /**
   * Enfileirar uma mensagem para envio.
   * @param {string} to        — número ou JID de destino
   * @param {string|object} content — texto ou objecto de mensagem Baileys
   * @param {object} opts
   * @param {string} [opts.sessionId]  — sessão preferida (opcional)
   * @param {number} [opts.priority]   — 1=alta, 10=normal, 100=baixa
   * @param {number} [opts.delay]      — atraso em ms antes de tentar
   */
  async enqueue(to, content, opts = {}) {
    const job = await this.outQueue.add(
      { to, content, sessionId: opts.sessionId || null, attempt: 0 },
      {
        priority:  opts.priority || 10,
        delay:     opts.delay    || 0,
        attempts:  5,                   // máximo de 5 tentativas
        backoff: {
          type:  'exponential',
          delay: 5000,                  // começa em 5s
        },
        removeOnComplete: 100,          // guardar últimos 100 jobs completos
        removeOnFail:     50,
      }
    );

    logger.info({ jobId: job.id, to }, '[Queue] Mensagem enfileirada');
    return job.id;
  }

  async getStats() {
    const [waiting, active, completed, failed] = await Promise.all([
      this.outQueue.getWaitingCount(),
      this.outQueue.getActiveCount(),
      this.outQueue.getCompletedCount(),
      this.outQueue.getFailedCount(),
    ]);
    return { waiting, active, completed, failed };
  }
}

module.exports = MessageQueue;
