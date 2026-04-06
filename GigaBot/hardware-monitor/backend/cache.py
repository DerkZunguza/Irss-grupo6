# cache.py — Camada Redis para cache de mensagens e estado de conversas
import os
import json
import redis
from datetime import datetime

# Instância partilhada (inicializada em app.py via init_cache)
_redis: redis.Redis | None = None

# TTLs
TTL_MSG_LIST   = 60 * 60 * 24 * 7   # 7 dias — lista de mensagens recentes
TTL_CONTACT    = 60 * 60             # 1 hora  — cache de contacto
TTL_CONV_STATE = 60 * 30             # 30 min  — estado de conversa (timeout de fluxo)
TTL_RECEIPT    = 60 * 60 * 24        # 24 horas — comprovativo pendente


def init_cache(app):
    """Inicializar ligação Redis a partir da configuração da app."""
    global _redis
    host = app.config.get('REDIS_HOST', 'redis-wa')
    port = app.config.get('REDIS_PORT', 6379)
    db   = app.config.get('REDIS_DB',   1)   # DB 0 é do Bull (Node.js), usar DB 1
    try:
        _redis = redis.Redis(host=host, port=port, db=db, decode_responses=True, socket_timeout=3)
        _redis.ping()
        app.logger.info(f'[Cache] Redis ligado em {host}:{port} db={db}')
    except redis.exceptions.ConnectionError:
        app.logger.warning('[Cache] Redis indisponível — cache desactivado')
        _redis = None


def _r() -> redis.Redis | None:
    return _redis


# ══════════════════════════════════════════════════════════════════════════════
# MENSAGENS — lista recente por número
# ══════════════════════════════════════════════════════════════════════════════

def push_message(phone: str, msg_dict: dict, max_items: int = 100):
    """
    Adiciona uma mensagem à lista recente do contacto no Redis.
    Mantém no máximo max_items entradas.
    """
    r = _r()
    if not r:
        return
    key = f'wa:msgs:{phone}'
    try:
        r.lpush(key, json.dumps(msg_dict, default=str))
        r.ltrim(key, 0, max_items - 1)
        r.expire(key, TTL_MSG_LIST)
    except Exception:
        pass


def get_recent_messages(phone: str, limit: int = 50) -> list:
    """Retorna as últimas `limit` mensagens do contacto directamente do Redis."""
    r = _r()
    if not r:
        return []
    key = f'wa:msgs:{phone}'
    try:
        raw = r.lrange(key, 0, limit - 1)
        return [json.loads(m) for m in raw]
    except Exception:
        return []


def increment_unread(phone: str):
    """Incrementa o contador de mensagens não lidas do contacto."""
    r = _r()
    if not r:
        return
    try:
        key = f'wa:unread:{phone}'
        r.incr(key)
        r.expire(key, TTL_MSG_LIST)
    except Exception:
        pass


def clear_unread(phone: str):
    """Zera o contador de não lidas (quando o utilizador abre a conversa)."""
    r = _r()
    if not r:
        return
    try:
        r.delete(f'wa:unread:{phone}')
    except Exception:
        pass


def get_unread_count(phone: str) -> int:
    r = _r()
    if not r:
        return 0
    try:
        val = r.get(f'wa:unread:{phone}')
        return int(val) if val else 0
    except Exception:
        return 0


# ══════════════════════════════════════════════════════════════════════════════
# ESTADO DE CONVERSA — fluxos multi-passo (ex.: envio de comprovativo)
# ══════════════════════════════════════════════════════════════════════════════

def set_conv_state(phone: str, state: str, data: dict = None):
    """
    Guarda o estado da conversa com um contacto.
    Exemplos de state: 'IDLE', 'WAITING_RECEIPT', 'WAITING_CONFIRM'
    """
    r = _r()
    if not r:
        return
    key = f'wa:state:{phone}'
    payload = {'state': state, 'data': data or {}, 'updated_at': datetime.utcnow().isoformat()}
    try:
        r.setex(key, TTL_CONV_STATE, json.dumps(payload))
    except Exception:
        pass


def get_conv_state(phone: str) -> dict:
    """Retorna {'state': '...', 'data': {...}} ou {'state': 'IDLE', 'data': {}}."""
    r = _r()
    if not r:
        return {'state': 'IDLE', 'data': {}}
    key = f'wa:state:{phone}'
    try:
        val = r.get(key)
        if val:
            return json.loads(val)
    except Exception:
        pass
    return {'state': 'IDLE', 'data': {}}


def clear_conv_state(phone: str):
    r = _r()
    if not r:
        return
    try:
        r.delete(f'wa:state:{phone}')
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# COMPROVATIVOS PENDENTES — fila de processamento assíncrono
# ══════════════════════════════════════════════════════════════════════════════

def queue_receipt(phone: str, receipt_data: dict):
    """
    Enfileira um comprovativo recebido para processamento.
    Usa um Sorted Set com score = timestamp para ordenação.
    """
    r = _r()
    if not r:
        return
    key = 'wa:receipts:pending'
    score = datetime.utcnow().timestamp()
    payload = json.dumps({'phone': phone, **receipt_data, 'queued_at': datetime.utcnow().isoformat()})
    try:
        r.zadd(key, {payload: score})
        r.expire(key, TTL_RECEIPT)
    except Exception:
        pass


def pop_pending_receipts(count: int = 10) -> list:
    """Remove e retorna os próximos `count` comprovativos a processar (FIFO)."""
    r = _r()
    if not r:
        return []
    key = 'wa:receipts:pending'
    try:
        # ZPOPMIN — retira os mais antigos primeiro
        items = r.zpopmin(key, count)
        return [json.loads(item[0]) for item in items]
    except Exception:
        return []


def count_pending_receipts() -> int:
    r = _r()
    if not r:
        return 0
    try:
        return r.zcard('wa:receipts:pending') or 0
    except Exception:
        return 0


# ══════════════════════════════════════════════════════════════════════════════
# CACHE DE CONTACTO — evitar query à BD em cada mensagem recebida
# ══════════════════════════════════════════════════════════════════════════════

def cache_contact(phone: str, contact_dict: dict):
    r = _r()
    if not r:
        return
    try:
        r.setex(f'wa:contact:{phone}', TTL_CONTACT, json.dumps(contact_dict))
    except Exception:
        pass


def get_cached_contact(phone: str) -> dict | None:
    r = _r()
    if not r:
        return None
    try:
        val = r.get(f'wa:contact:{phone}')
        return json.loads(val) if val else None
    except Exception:
        return None


def invalidate_contact(phone: str):
    r = _r()
    if not r:
        return
    try:
        r.delete(f'wa:contact:{phone}')
    except Exception:
        pass
