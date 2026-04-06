// sessions/SessionManager.js — Pool de sessões Baileys com reconexão automática
'use strict';

const {
  default: makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
  makeInMemoryStore,
  Browsers,
} = require('@whiskeysockets/baileys');

const path    = require('path');
const fs      = require('fs');
const pino    = require('pino');
const QRCode  = require('qrcode');
const logger  = require('../utils/logger');

// Estados possíveis de cada sessão
const STATE = {
  STARTING:       'starting',
  QR_PENDING:     'qr_pending',  // aguardar scan do QR
  CONNECTED:      'connected',
  RECONNECTING:   'reconnecting',
  CLOSED:         'closed',
};

class Session {
  constructor(id, sessionsDir) {
    this.id           = id;
    this.authDir      = path.join(sessionsDir, `session-${id}`);
    this.state        = STATE.STARTING;
    this.socket       = null;
    this.qrCode       = null;       // base64 do QR actual
    this.retries      = 0;
    this.maxRetries   = 10;
    this.retryDelay   = 5000;       // ms — cresce exponencialmente
    this._listeners   = {};         // callbacks registados
  }

  on(event, fn) {
    if (!this._listeners[event]) this._listeners[event] = [];
    this._listeners[event].push(fn);
  }

  emit(event, ...args) {
    (this._listeners[event] || []).forEach(fn => fn(...args));
  }
}


class SessionManager {
  /**
   * @param {object} opts
   * @param {number} opts.poolSize      — número de sessões paralelas
   * @param {string} opts.sessionsDir   — directório para guardar auth state
   * @param {function} opts.onMessage   — callback(sessionId, message)
   */
  constructor({ poolSize = 2, sessionsDir, onMessage }) {
    this.poolSize    = poolSize;
    this.sessionsDir = sessionsDir || path.join(process.cwd(), 'sessions-data');
    this.onMessage   = onMessage || (() => {});
    this.sessions    = new Map(); // id → Session

    fs.mkdirSync(this.sessionsDir, { recursive: true });
  }

  // ── Inicializar pool completo ──────────────────────────────────────────────
  async init() {
    logger.info(`[SessionManager] Iniciando pool de ${this.poolSize} sessão(ões)...`);
    const ids = Array.from({ length: this.poolSize }, (_, i) => `s${i + 1}`);
    await Promise.all(ids.map(id => this._startSession(id)));
  }

  // ── Arrancar/reconectar uma sessão individual ──────────────────────────────
  async _startSession(id) {
    const session = this.sessions.get(id) || new Session(id, this.sessionsDir);
    this.sessions.set(id, session);
    session.state = STATE.STARTING;

    logger.info(`[Sessão ${id}] A iniciar...`);

    try {
      const { version } = await fetchLatestBaileysVersion();
      const { state: authState, saveCreds } = await useMultiFileAuthState(session.authDir);

      // Logger silencioso para não poluir output (Baileys é verboso)
      const waLogger = pino({ level: 'silent' });

      const sock = makeWASocket({
        version,
        auth:            authState,
        logger:          waLogger,
        browser:         Browsers.ubuntu('GigaBot'),
        printQRInTerminal: false,       // gerimos o QR aqui
        connectTimeoutMs: 30_000,
        keepAliveIntervalMs: 15_000,    // heartbeat mais frequente
        retryRequestDelayMs: 500,
      });

      session.socket = sock;

      // ── Guardar credenciais sempre que actualizadas ──────────────────────
      sock.ev.on('creds.update', saveCreds);

      // ── Mudança de estado da conexão ─────────────────────────────────────
      sock.ev.on('connection.update', async (update) => {
        const { connection, lastDisconnect, qr } = update;

        // Novo QR gerado
        if (qr) {
          session.state  = STATE.QR_PENDING;
          session.qrCode = await QRCode.toDataURL(qr);
          logger.info(`[Sessão ${id}] QR disponível — aceder /api/sessions/${id}/qr`);
          session.emit('qr', session.qrCode);
        }

        if (connection === 'open') {
          session.state   = STATE.CONNECTED;
          session.retries = 0;
          session.qrCode  = null;
          logger.info(`[Sessão ${id}] Conectada com sucesso ✓`);
          session.emit('connected');
        }

        if (connection === 'close') {
          const code   = lastDisconnect?.error?.output?.statusCode;
          const reason = DisconnectReason;

          // Logout explícito — não reconectar, apagar credenciais
          if (code === reason.loggedOut) {
            logger.warn(`[Sessão ${id}] Desconectada por logout — a limpar credenciais...`);
            session.state = STATE.CLOSED;
            fs.rmSync(session.authDir, { recursive: true, force: true });
            // Reiniciar do zero após 2s
            setTimeout(() => this._startSession(id), 2000);
            return;
          }

          // Qualquer outro motivo — reconectar com backoff exponencial
          if (session.retries < session.maxRetries) {
            const delay = Math.min(session.retryDelay * Math.pow(2, session.retries), 60_000);
            session.retries++;
            session.state = STATE.RECONNECTING;
            logger.warn(`[Sessão ${id}] Desconectada (código ${code}) — tentativa ${session.retries}/${session.maxRetries} em ${delay / 1000}s`);
            session.emit('disconnected', { code, retries: session.retries });
            setTimeout(() => this._startSession(id), delay);
          } else {
            logger.error(`[Sessão ${id}] Máximo de tentativas atingido — sessão em estado CLOSED`);
            session.state = STATE.CLOSED;
            session.emit('failed');
          }
        }
      });

      // ── Mensagens recebidas ───────────────────────────────────────────────
      sock.ev.on('messages.upsert', ({ messages, type }) => {
        if (type !== 'notify') return;

        for (const msg of messages) {
          if (!msg.message || msg.key.fromMe) continue; // ignorar próprias mensagens

          const content = this._extractText(msg);
          if (!content) continue;

          const payload = {
            sessionId:   id,
            from:        msg.key.remoteJid,
            body:        content,
            timestamp:   msg.messageTimestamp,
            isGroup:     msg.key.remoteJid?.endsWith('@g.us') || false,
            messageId:   msg.key.id,
          };

          logger.info(`[Sessão ${id}] Mensagem de ${payload.from}: "${content.substring(0, 50)}"`);
          this.onMessage(payload);
        }
      });

    } catch (err) {
      logger.error({ err }, `[Sessão ${id}] Erro ao iniciar`);
      const delay = Math.min(5000 * Math.pow(2, session.retries), 60_000);
      session.retries++;
      setTimeout(() => this._startSession(id), delay);
    }
  }

  // ── Extrair texto de qualquer tipo de mensagem ────────────────────────────
  _extractText(msg) {
    const m = msg.message;
    return (
      m?.conversation ||
      m?.extendedTextMessage?.text ||
      m?.imageMessage?.caption ||
      m?.videoMessage?.caption ||
      m?.buttonsResponseMessage?.selectedButtonId ||
      m?.listResponseMessage?.singleSelectReply?.selectedRowId ||
      null
    );
  }

  // ── Enviar mensagem usando a primeira sessão disponível ───────────────────
  async sendMessage(to, content, preferredSessionId = null) {
    const jid = this._normalizeJid(to);

    // Tentar sessão preferida primeiro
    if (preferredSessionId) {
      const preferred = this.sessions.get(preferredSessionId);
      if (preferred?.state === STATE.CONNECTED) {
        return this._send(preferred, jid, content);
      }
    }

    // Fallback para qualquer sessão conectada
    for (const session of this.sessions.values()) {
      if (session.state === STATE.CONNECTED) {
        return this._send(session, jid, content);
      }
    }

    throw new Error('Nenhuma sessão WhatsApp disponível de momento');
  }

  async _send(session, jid, content) {
    // Suporta texto simples ou objecto de mensagem complexo
    const messageContent = typeof content === 'string'
      ? { text: content }
      : content;

    const result = await session.socket.sendMessage(jid, messageContent);
    logger.info(`[Sessão ${session.id}] Mensagem enviada para ${jid}`);
    return { sessionId: session.id, messageId: result.key.id };
  }

  // ── Normalizar número para JID do WhatsApp ────────────────────────────────
  _normalizeJid(number) {
    // Remover qualquer formatação e adicionar sufixo @s.whatsapp.net
    const clean = String(number).replace(/\D/g, '');
    if (clean.endsWith('@s.whatsapp.net') || clean.endsWith('@g.us')) return clean;
    return `${clean}@s.whatsapp.net`;
  }

  // ── Estado do pool para a API ─────────────────────────────────────────────
  getStatus() {
    const sessions = [];
    for (const [id, s] of this.sessions) {
      sessions.push({
        id,
        state:    s.state,
        retries:  s.retries,
        hasQR:    !!s.qrCode,
      });
    }
    const connected = sessions.filter(s => s.state === STATE.CONNECTED).length;
    return { poolSize: this.poolSize, connected, sessions };
  }

  getSession(id) {
    return this.sessions.get(id) || null;
  }

  getQR(id) {
    return this.sessions.get(id)?.qrCode || null;
  }
}

module.exports = { SessionManager, STATE };
