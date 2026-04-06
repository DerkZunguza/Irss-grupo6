# routes/sms.py — Endpoints de gestão de SMS via SIM900 / ESP32-S3 relay
from flask import Blueprint, request, jsonify
from datetime import datetime

from models import db, Device, SmsMessage

sms_bp = Blueprint('sms', __name__)

# Limite de mensagens no SIM900 (memória do módulo)
SIM900_MAX_MESSAGES = 20


# ── Helpers ────────────────────────────────────────────────────────────────────

def ok(data=None, message='OK', status=200):
    return jsonify({'success': True,  'data': data, 'message': message}), status

def err(message, status=400):
    return jsonify({'success': False, 'data': None, 'message': message}), status


# ── POST /api/sms/sync ────────────────────────────────────────────────────────
@sms_bp.route('/sms/sync', methods=['POST'])
def sync_sms():
    """
    ESP32 envia lote de até 20 SMS recebidos pelo SIM900 para guardar no servidor.
    Corpo: {
      "device_id": 1,
      "messages": [
        { "direction": "IN", "phone": "+258...", "body": "texto", "received_at": "..." }
      ]
    }
    """
    data = request.get_json(silent=True)
    if not data:
        return err('Corpo da requisição inválido ou vazio')

    device_id = data.get('device_id')
    messages  = data.get('messages')

    if device_id is None or messages is None:
        return err("Os campos 'device_id' e 'messages' são obrigatórios")

    device = Device.query.get(device_id)
    if not device:
        return err('Dispositivo não encontrado', 404)

    if not isinstance(messages, list):
        return err("'messages' deve ser uma lista de objectos")

    if len(messages) > SIM900_MAX_MESSAGES:
        return err(f'Máximo de {SIM900_MAX_MESSAGES} mensagens por sincronização')

    saved_count = 0
    for msg in messages:
        direction = (msg.get('direction') or 'IN').upper()
        phone     = (msg.get('phone') or '').strip()
        body      = (msg.get('body')  or '').strip()

        if not phone or not body:
            continue  # ignorar mensagens incompletas

        # Converter timestamp se fornecido
        received_at = None
        if msg.get('received_at'):
            try:
                received_at = datetime.fromisoformat(
                    str(msg['received_at']).replace('Z', '+00:00')
                )
            except ValueError:
                received_at = datetime.utcnow()
        else:
            received_at = datetime.utcnow()

        sms = SmsMessage(
            device_id=device_id,
            direction=direction,
            phone_number=phone,
            message_body=body,
            received_at=received_at,
            synced_at=datetime.utcnow(),
        )
        db.session.add(sms)
        saved_count += 1

    db.session.commit()
    return ok({'saved': saved_count}, f'{saved_count} SMS sincronizados com sucesso', 201)


# ── GET /api/sms ──────────────────────────────────────────────────────────────
@sms_bp.route('/sms', methods=['GET'])
def get_sms():
    """
    Lista mensagens SMS.
    Query params: device_id, phone, limit (padrão 100)
    """
    device_id = request.args.get('device_id', type=int)
    phone     = request.args.get('phone', '').strip() or None
    limit     = min(request.args.get('limit', 100, type=int), 500)

    query = SmsMessage.query
    if device_id:
        query = query.filter_by(device_id=device_id)
    if phone:
        query = query.filter_by(phone_number=phone)

    messages = query.order_by(SmsMessage.received_at.desc()).limit(limit).all()
    return ok([m.to_dict() for m in messages])


# ── POST /api/sms/send ────────────────────────────────────────────────────────
@sms_bp.route('/sms/send', methods=['POST'])
def send_sms():
    """
    Enfileira um SMS para ser enviado pelo SIM900 via ESP32.
    Corpo: { "device_id": 1, "phone": "+258...", "body": "texto" }
    O ESP32 faz polling dos SMS pendentes e envia via AT commands.
    """
    data = request.get_json(silent=True)
    if not data:
        return err('Corpo da requisição inválido ou vazio')

    device_id = data.get('device_id')
    phone     = (data.get('phone') or '').strip()
    body      = (data.get('body')  or '').strip()

    if not device_id:
        return err("O campo 'device_id' é obrigatório")
    if not phone:
        return err("O campo 'phone' é obrigatório")
    if not body:
        return err("O campo 'body' não pode estar vazio")
    if len(body) > 160:
        return err('Mensagem SMS não pode exceder 160 caracteres')

    device = Device.query.get(device_id)
    if not device:
        return err('Dispositivo não encontrado', 404)

    sms = SmsMessage(
        device_id=device_id,
        direction='OUT',
        phone_number=phone,
        message_body=body,
        received_at=datetime.utcnow(),
        sent=False,
    )
    db.session.add(sms)
    db.session.commit()
    return ok(sms.to_dict(), 'SMS enfileirado para envio', 201)


# ── GET /api/sms/pending-send ─────────────────────────────────────────────────
@sms_bp.route('/sms/pending-send', methods=['GET'])
def pending_send():
    """
    ESP32 faz polling aqui para buscar SMS OUT ainda não enviados.
    Query params: device_id (obrigatório)
    """
    device_id = request.args.get('device_id', type=int)
    if not device_id:
        return err("Parâmetro 'device_id' é obrigatório")

    messages = (
        SmsMessage.query
        .filter_by(device_id=device_id, direction='OUT', sent=False)
        .order_by(SmsMessage.created_at.asc())
        .limit(SIM900_MAX_MESSAGES)
        .all()
    )
    return ok([m.to_dict() for m in messages])


# ── POST /api/sms/<id>/sent-ack ───────────────────────────────────────────────
@sms_bp.route('/sms/<int:sms_id>/sent-ack', methods=['POST'])
def sent_ack(sms_id):
    """ESP32 confirma que o SMS foi enviado com sucesso pelo SIM900."""
    sms = SmsMessage.query.get(sms_id)
    if not sms:
        return err('Mensagem não encontrada', 404)

    sms.sent = True
    db.session.commit()
    return ok(sms.to_dict(), 'SMS marcado como enviado')
