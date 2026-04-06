// api/routes.js — API REST do serviço WhatsApp
'use strict';

const express = require('express');
const { STATE } = require('../sessions/SessionManager');
const logger  = require('../utils/logger');

module.exports = function createRouter(sessionManager, messageQueue) {
  const router = express.Router();

  // ── Middleware de autenticação via token ───────────────────────────────────
  router.use((req, res, next) => {
    const token = req.headers['x-api-token'] || req.query.token;
    if (process.env.API_TOKEN && token !== process.env.API_TOKEN) {
      return res.status(401).json({ success: false, message: 'Token inválido' });
    }
    next();
  });

  // ── GET /status — estado do pool de sessões ───────────────────────────────
  router.get('/status', (req, res) => {
    res.json({ success: true, data: sessionManager.getStatus() });
  });

  // ── GET /sessions/:id/qr — imagem QR para escanear ───────────────────────
  router.get('/sessions/:id/qr', (req, res) => {
    const session = sessionManager.getSession(req.params.id);
    if (!session) {
      return res.status(404).json({ success: false, message: 'Sessão não encontrada' });
    }
    if (session.state === STATE.CONNECTED) {
      return res.json({ success: true, message: 'Sessão já está conectada', qr: null });
    }
    const qr = sessionManager.getQR(req.params.id);
    if (!qr) {
      return res.status(202).json({
        success: false,
        message: `Sessão em estado '${session.state}' — QR ainda não disponível. Tente novamente em alguns segundos.`,
        state: session.state,
      });
    }
    // Devolver como HTML para scan fácil no browser
    if (req.query.format === 'html') {
      return res.send(`
        <!DOCTYPE html><html><head><title>QR Sessão ${req.params.id}</title>
        <meta http-equiv="refresh" content="30">
        <style>body{background:#0f1117;display:flex;align-items:center;justify-content:center;height:100vh;flex-direction:column;font-family:sans-serif;color:#e8eaf0;}
        img{border:4px solid #00d4aa;border-radius:12px;}p{color:#8b93b0;margin-top:1rem;}</style></head>
        <body><h2 style="color:#00d4aa">Sessão ${req.params.id} — Escanear QR</h2>
        <img src="${qr}" width="280" height="280" />
        <p>A página actualiza automaticamente a cada 30s</p></body></html>
      `);
    }
    res.json({ success: true, data: { sessionId: req.params.id, qr, state: session.state } });
  });

  // ── POST /send — enviar mensagem (com fila) ───────────────────────────────
  router.post('/send', async (req, res) => {
    const { to, message, sessionId, priority } = req.body;

    if (!to || !message) {
      return res.status(400).json({
        success: false,
        message: "Os campos 'to' e 'message' são obrigatórios",
      });
    }

    try {
      const jobId = await messageQueue.enqueue(to, message, { sessionId, priority });
      res.json({ success: true, data: { jobId }, message: 'Mensagem enfileirada para envio' });
    } catch (err) {
      logger.error({ err }, '/send erro');
      res.status(500).json({ success: false, message: err.message });
    }
  });

  // ── POST /send-now — envio directo sem fila (urgente) ────────────────────
  router.post('/send-now', async (req, res) => {
    const { to, message, sessionId } = req.body;

    if (!to || !message) {
      return res.status(400).json({
        success: false,
        message: "Os campos 'to' e 'message' são obrigatórios",
      });
    }

    try {
      const result = await sessionManager.sendMessage(to, message, sessionId);
      res.json({ success: true, data: result, message: 'Mensagem enviada' });
    } catch (err) {
      res.status(503).json({ success: false, message: err.message });
    }
  });

  // ── GET /queue/stats — estado da fila ────────────────────────────────────
  router.get('/queue/stats', async (req, res) => {
    const stats = await messageQueue.getStats();
    res.json({ success: true, data: stats });
  });

  // ── Health check (sem autenticação) ──────────────────────────────────────
  router.get('/health', (req, res) => {
    const status  = sessionManager.getStatus();
    const healthy = status.connected > 0;
    res.status(healthy ? 200 : 503).json({
      success: healthy,
      data:    { connected: status.connected, poolSize: status.poolSize },
      message: healthy ? 'Serviço operacional' : 'Nenhuma sessão activa',
    });
  });

  return router;
};
