# routes/whatsapp.py — Recepção e envio de mensagens WhatsApp
# Integra com whatsapp-service (Baileys), guarda na BD e usa Redis como cache
import os
import requests
from flask import Blueprint, request, jsonify
from datetime import datetime

from models import db, Device, Contact, SmsMessage, DeviceCommand
import cache as redis_cache

whatsapp_bp = Blueprint('whatsapp', __name__)

WA_SERVICE_URL = os.environ.get('WA_SERVICE_URL', 'http://whatsapp-service:3001/api')
WA_API_TOKEN   = os.environ.get('WA_API_TOKEN',   'token_secreto')


def ok(data=None, message='OK', status=200):
    return jsonify({'success': True,  'data': data, 'message': message}), status

def err(message, status=400):
    return jsonify({'success': False, 'data': None, 'message': message}), status


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _wa_send(to: str, message: str, session_id: str = None):
    """Envia mensagem via whatsapp-service (com fila Redis/Bull)."""
    try:
        payload = {'to': to, 'message': message}
        if session_id:
            payload['sessionId'] = session_id
        requests.post(
            f'{WA_SERVICE_URL}/send',
            json=payload,
            headers={'x-api-token': WA_API_TOKEN},
            timeout=5,
        )
    except Exception as e:
        print(f'[WA] Falha ao enfileirar resposta: {e}')


def _get_or_create_contact(phone: str) -> Contact:
    """
    Retorna o contacto pelo número. Cria automaticamente se não existir.
    Usa cache Redis para evitar query à BD em cada mensagem.
    """
    # Verificar cache primeiro
    cached = redis_cache.get_cached_contact(phone)
    if cached:
        # Ainda precisamos do objecto ORM para relações — só usamos o cache para saber se existe
        contact = Contact.query.filter_by(phone_number=phone).first()
        if contact:
            return contact

    contact = Contact.query.filter_by(phone_number=phone).first()
    if not contact:
        contact = Contact(phone_number=phone, name=None)
        db.session.add(contact)
        db.session.flush()  # obter ID sem commit completo

    # Actualizar cache
    redis_cache.cache_contact(phone, contact.to_dict())
    return contact


def _save_message(contact: Contact, direction: str, body: str,
                  media_url: str = None, media_type: str = None,
                  timestamp=None) -> SmsMessage:
    """Persiste a mensagem na BD e actualiza o cache Redis."""
    received_at = datetime.utcfromtimestamp(timestamp) if timestamp else datetime.utcnow()
    device = contact.device or Device.query.first()

    msg = SmsMessage(
        device_id    = device.id if device else None,
        contact_id   = contact.id,
        channel      = 'WHATSAPP',
        direction    = direction,
        phone_number = contact.phone_number,
        message_body = body,
        media_url    = media_url,
        media_type   = media_type,
        received_at  = received_at,
        synced_at    = datetime.utcnow(),
        sent         = (direction == 'OUT'),
    )
    db.session.add(msg)
    db.session.commit()

    # Adicionar ao cache Redis (lista recente do contacto)
    redis_cache.push_message(contact.phone_number, msg.to_dict())
    if direction == 'IN':
        redis_cache.increment_unread(contact.phone_number)

    return msg


# ══════════════════════════════════════════════════════════════════════════════
# PROCESSAMENTO DE COMANDOS (fluxo de conversa multi-passo)
# ══════════════════════════════════════════════════════════════════════════════

COMANDOS_INFO = {
    'status':    'Estado de todos os dispositivos',
    'temp':      'Última temperatura e humidade',
    'led1 on':   'Liga LED1',  'led1 off':  'Desliga LED1',
    'led2 on':   'Liga LED2',  'led2 off':  'Desliga LED2',
    'relay on':  'Liga relé',  'relay off': 'Desliga relé',
    'comprovativo': 'Enviar comprovativo de compra',
    'ajuda':     'Lista de comandos',
}

TARGET_MAP = {
    'led1 on':  ('LED1', 'ON'),  'led1 off':  ('LED1', 'OFF'),
    'led2 on':  ('LED2', 'ON'),  'led2 off':  ('LED2', 'OFF'),
    'relay on': ('RELAY','ON'),  'relay off': ('RELAY','OFF'),
}


def _process_message(contact: Contact, body: str, media_url: str = None,
                     media_type: str = None) -> str:
    """
    Processa a mensagem recebida segundo o estado da conversa.
    Retorna a resposta em texto para enviar de volta ao contacto.
    """
    phone = contact.phone_number
    cmd   = body.strip().lower()
    state = redis_cache.get_conv_state(phone)

    # ── Fluxo de comprovativo ─────────────────────────────────────────────────
    if state['state'] == 'WAITING_RECEIPT':
        if media_url:
            # Comprovativo recebido — enfileirar no Redis para processamento
            redis_cache.queue_receipt(phone, {
                'contact_id':   contact.id,
                'contact_name': contact.name or phone,
                'media_url':    media_url,
                'media_type':   media_type or 'image',
                'message':      body,
            })
            redis_cache.clear_conv_state(phone)
            return (
                '✅ Comprovativo recebido e registado!\n'
                'A nossa equipa irá verificar em breve.\n'
                f'Referência: `REC-{contact.id}-{int(datetime.utcnow().timestamp())}`'
            )
        elif cmd == 'cancelar':
            redis_cache.clear_conv_state(phone)
            return '❌ Envio de comprovativo cancelado.'
        else:
            return '📎 Por favor envie a *imagem* do comprovativo, ou envie *cancelar* para desistir.'

    # ── Comandos normais ──────────────────────────────────────────────────────
    if cmd == 'ajuda':
        linhas = ['*Comandos disponíveis:*']
        for c, desc in COMANDOS_INFO.items():
            linhas.append(f'• `{c}` — {desc}')
        return '\n'.join(linhas)

    if cmd == 'status':
        devices = Device.query.all()
        if not devices:
            return 'Nenhum dispositivo registado.'
        linhas = ['*Estado dos dispositivos:*']
        for d in devices:
            icon = '🟢' if d.status == 'online' else '🔴'
            ts   = d.last_seen.strftime('%d/%m %H:%M') if d.last_seen else '—'
            linhas.append(f'{icon} *{d.name}* ({d.type}) — {ts}')
        return '\n'.join(linhas)

    if cmd == 'temp':
        from models import SensorReading
        devices = Device.query.all()
        if not devices:
            return 'Nenhum dispositivo registado.'
        linhas = ['*Última leitura de sensores:*']
        for d in devices:
            r = d.sensor_readings.order_by(SensorReading.recorded_at.desc()).first()
            if r:
                linhas.append(f'🌡️ *{d.name}*: {r.temperature}°C  💧 {r.humidity}%')
            else:
                linhas.append(f'⚠️ *{d.name}*: sem leituras')
        return '\n'.join(linhas)

    if cmd == 'comprovativo':
        redis_cache.set_conv_state(phone, 'WAITING_RECEIPT', {'contact_id': contact.id})
        return '📎 Envie a *imagem* do comprovativo de compra.\nEnvie *cancelar* para desistir.'

    if cmd in TARGET_MAP:
        target, state_val = TARGET_MAP[cmd]
        device = (contact.device or Device.query.filter_by(status='online').first())
        if not device:
            return '⚠️ Nenhum dispositivo online de momento.'
        command = DeviceCommand(device_id=device.id, command=state_val, target=target)
        db.session.add(command)
        db.session.commit()
        return f'✅ Comando *{state_val}* → *{target}* enviado a *{device.name}*.'

    # Imagem recebida sem estado de conversa activo
    if media_url and media_type and 'image' in media_type:
        return (
            '📸 Imagem recebida.\n'
            'Para enviar um comprovativo de compra, envie primeiro o comando *comprovativo*.'
        )

    return f'❓ Não entendi: `{body}`\nEnvie *ajuda* para ver os comandos disponíveis.'


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

# ── POST /api/whatsapp/incoming — webhook do whatsapp-service ─────────────────
@whatsapp_bp.route('/whatsapp/incoming', methods=['POST'])
def incoming():
    """
    Recebe mensagens do whatsapp-service Baileys.
    1. Cria/encontra contacto
    2. Guarda mensagem na BD + Redis
    3. Processa comando / fluxo
    4. Envia resposta
    """
    data = request.get_json(silent=True)
    if not data:
        return err('Payload inválido')

    sender     = data.get('from', '').replace('@s.whatsapp.net', '').replace('@g.us', '')
    body       = (data.get('body') or '').strip()
    session_id = data.get('sessionId')
    timestamp  = data.get('timestamp')
    media_url  = data.get('mediaUrl')
    media_type = data.get('mediaType')
    is_group   = data.get('isGroup', False)

    # Ignorar grupos por agora
    if is_group or not sender:
        return ok(None, 'Ignorado')

    # 1. Obter ou criar contacto
    contact = _get_or_create_contact(sender)

    # 2. Guardar mensagem na BD + cache
    msg = _save_message(contact, 'IN', body or '[mídia]',
                        media_url=media_url, media_type=media_type,
                        timestamp=timestamp)

    # 3. Processar e responder (só se tiver texto ou mídia relevante)
    if body or media_url:
        resposta = _process_message(contact, body, media_url=media_url, media_type=media_type)
        _wa_send(sender, resposta, session_id)

        # Guardar resposta enviada na BD + cache também
        _save_message(contact, 'OUT', resposta)

    return ok({'messageId': msg.id}, 'Processado')


# ── POST /api/whatsapp/send ───────────────────────────────────────────────────
@whatsapp_bp.route('/whatsapp/send', methods=['POST'])
def send():
    """
    Envia uma mensagem WhatsApp manualmente (ex.: notificação do sistema).
    Guarda na BD associada ao contacto.
    """
    data = request.get_json(silent=True)
    if not data or not data.get('to') or not data.get('message'):
        return err("Os campos 'to' e 'message' são obrigatórios")

    phone   = data['to'].replace('@s.whatsapp.net', '')
    message = data['message']

    # Guardar na BD antes de enviar
    contact = _get_or_create_contact(phone)
    msg = _save_message(contact, 'OUT', message)

    # Enviar via whatsapp-service
    try:
        resp = requests.post(
            f'{WA_SERVICE_URL}/send',
            json={'to': phone, 'message': message},
            headers={'x-api-token': WA_API_TOKEN},
            timeout=8,
        )
        result = resp.json()
        result['dbMessageId'] = msg.id
        return jsonify(result), resp.status_code
    except requests.exceptions.ConnectionError:
        return err('Serviço WhatsApp indisponível', 503)


# ── GET /api/whatsapp/status ──────────────────────────────────────────────────
@whatsapp_bp.route('/whatsapp/status', methods=['GET'])
def status():
    try:
        resp = requests.get(f'{WA_SERVICE_URL}/status',
                            headers={'x-api-token': WA_API_TOKEN}, timeout=5)
        return jsonify(resp.json()), resp.status_code
    except requests.exceptions.ConnectionError:
        return err('Serviço WhatsApp indisponível', 503)
