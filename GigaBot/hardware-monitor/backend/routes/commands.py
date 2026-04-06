# routes/commands.py — Endpoints de controlo de LEDs e relés
from flask import Blueprint, request, jsonify

from models import db, Device, DeviceCommand

commands_bp = Blueprint('commands', __name__)

VALID_TARGETS   = {'LED1', 'LED2', 'RELAY'}
VALID_COMMANDS  = {'ON', 'OFF'}


# ── Helpers ────────────────────────────────────────────────────────────────────

def ok(data=None, message='OK', status=200):
    return jsonify({'success': True,  'data': data, 'message': message}), status

def err(message, status=400):
    return jsonify({'success': False, 'data': None, 'message': message}), status


# ── POST /api/devices/<id>/control ────────────────────────────────────────────
@commands_bp.route('/devices/<int:device_id>/control', methods=['POST'])
def control_device(device_id):
    """
    Enfileira um comando de controlo para o dispositivo.
    Corpo: { "target": "LED1", "state": "ON" }
    """
    device = Device.query.get(device_id)
    if not device:
        return err('Dispositivo não encontrado', 404)

    data = request.get_json(silent=True)
    if not data:
        return err('Corpo da requisição inválido ou vazio')

    target = (data.get('target') or '').upper().strip()
    state  = (data.get('state')  or '').upper().strip()

    if target not in VALID_TARGETS:
        return err(f"Target inválido. Use: {', '.join(sorted(VALID_TARGETS))}")
    if state not in VALID_COMMANDS:
        return err(f"Estado inválido. Use: {', '.join(sorted(VALID_COMMANDS))}")

    command = DeviceCommand(
        device_id=device_id,
        command=state,
        target=target,
    )
    db.session.add(command)
    db.session.commit()

    return ok(command.to_dict(), f'Comando {state} para {target} enfileirado', 201)


# ── GET /api/devices/<id>/pending-commands ────────────────────────────────────
@commands_bp.route('/devices/<int:device_id>/pending-commands', methods=['GET'])
def pending_commands(device_id):
    """
    ESP32 faz polling aqui a cada 5 segundos para buscar comandos pendentes.
    Retorna apenas comandos ainda não executados, por ordem de criação.
    """
    device = Device.query.get(device_id)
    if not device:
        return err('Dispositivo não encontrado', 404)

    commands = (
        DeviceCommand.query
        .filter_by(device_id=device_id, executed=False)
        .order_by(DeviceCommand.created_at.asc())
        .all()
    )
    return ok([c.to_dict() for c in commands])


# ── POST /api/devices/<id>/command-ack/<cmd_id> ───────────────────────────────
@commands_bp.route('/devices/<int:device_id>/command-ack/<int:cmd_id>', methods=['POST'])
def command_ack(device_id, cmd_id):
    """
    ESP32 confirma que executou o comando.
    Marca o comando como executed=True.
    """
    command = DeviceCommand.query.filter_by(
        id=cmd_id, device_id=device_id
    ).first()

    if not command:
        return err('Comando não encontrado', 404)

    if command.executed:
        return ok(command.to_dict(), 'Comando já estava marcado como executado')

    command.executed = True
    db.session.commit()
    return ok(command.to_dict(), 'Comando confirmado como executado')


# ── GET /api/devices/<id>/commands ────────────────────────────────────────────
@commands_bp.route('/devices/<int:device_id>/commands', methods=['GET'])
def all_commands(device_id):
    """
    Histórico de todos os comandos (executados e pendentes).
    Query params: limit (padrão 100), status (all | executed | pending)
    Usado pela página de Logs do frontend.
    """
    device = Device.query.get(device_id)
    if not device:
        return err('Dispositivo não encontrado', 404)

    limit  = min(request.args.get('limit', 100, type=int), 500)
    status = request.args.get('status', 'all').lower()

    query = DeviceCommand.query.filter_by(device_id=device_id)
    if status == 'executed':
        query = query.filter_by(executed=True)
    elif status == 'pending':
        query = query.filter_by(executed=False)

    commands = query.order_by(DeviceCommand.created_at.desc()).limit(limit).all()
    return ok([c.to_dict() for c in commands])
