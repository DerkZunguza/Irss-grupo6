# routes/devices.py — Endpoints de gestão de dispositivos e heartbeat
from flask import Blueprint, request, jsonify
from datetime import datetime, timedelta

from models import db, Device

devices_bp = Blueprint('devices', __name__)


# ── Helpers ────────────────────────────────────────────────────────────────────

def ok(data=None, message='OK', status=200):
    return jsonify({'success': True,  'data': data, 'message': message}), status

def err(message, status=400):
    return jsonify({'success': False, 'data': None, 'message': message}), status


def _mark_offline_devices():
    """Marca como offline dispositivos que não enviaram heartbeat há mais de 2 min."""
    limite = datetime.utcnow() - timedelta(minutes=2)
    Device.query.filter(
        Device.status == 'online',
        Device.last_seen < limite
    ).update({'status': 'offline'})
    db.session.commit()


# ── GET /api/devices ───────────────────────────────────────────────────────────
@devices_bp.route('/devices', methods=['GET'])
def get_devices():
    """Lista todos os dispositivos registados."""
    _mark_offline_devices()
    devices = Device.query.order_by(Device.created_at).all()
    return ok([d.to_dict() for d in devices])


# ── POST /api/devices ──────────────────────────────────────────────────────────
@devices_bp.route('/devices', methods=['POST'])
def create_device():
    """Regista um novo dispositivo."""
    data = request.get_json(silent=True)
    if not data:
        return err('Corpo da requisição inválido ou vazio')

    name = (data.get('name') or '').strip()
    dtype = (data.get('type') or '').strip()

    if not name or not dtype:
        return err("Os campos 'name' e 'type' são obrigatórios")

    device = Device(
        name=name,
        type=dtype,
        ip_address=data.get('ip_address'),
        status='offline',
    )
    db.session.add(device)
    db.session.commit()
    return ok(device.to_dict(), 'Dispositivo criado com sucesso', 201)


# ── GET /api/devices/<id> ──────────────────────────────────────────────────────
@devices_bp.route('/devices/<int:device_id>', methods=['GET'])
def get_device(device_id):
    """Retorna detalhes de um dispositivo específico."""
    device = Device.query.get(device_id)
    if not device:
        return err('Dispositivo não encontrado', 404)
    return ok(device.to_dict())


# ── DELETE /api/devices/<id> ───────────────────────────────────────────────────
@devices_bp.route('/devices/<int:device_id>', methods=['DELETE'])
def delete_device(device_id):
    """Remove um dispositivo e todos os seus dados associados."""
    device = Device.query.get(device_id)
    if not device:
        return err('Dispositivo não encontrado', 404)
    db.session.delete(device)
    db.session.commit()
    return ok(None, 'Dispositivo removido com sucesso')


# ── POST /api/devices/<id>/heartbeat ──────────────────────────────────────────
@devices_bp.route('/devices/<int:device_id>/heartbeat', methods=['POST'])
def heartbeat(device_id):
    """
    ESP32 envia heartbeat a cada 30 segundos para indicar que está online.
    Corpo opcional: { "ip": "192.168.1.x", "lcd_message": "Temp:28C Hum:65%" }
    """
    device = Device.query.get(device_id)
    if not device:
        return err('Dispositivo não encontrado', 404)

    data = request.get_json(silent=True) or {}
    device.status    = 'online'
    device.last_seen = datetime.utcnow()

    if data.get('ip'):
        device.ip_address = data['ip'].strip()

    db.session.commit()
    return ok({'last_seen': device.last_seen.isoformat() + 'Z'}, 'Heartbeat registado')
