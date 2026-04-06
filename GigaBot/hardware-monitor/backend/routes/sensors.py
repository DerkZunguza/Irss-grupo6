# routes/sensors.py — Endpoints de leituras do sensor DHT11
from flask import Blueprint, request, jsonify
from datetime import datetime

from models import db, Device, SensorReading

sensors_bp = Blueprint('sensors', __name__)


# ── Helpers ────────────────────────────────────────────────────────────────────

def ok(data=None, message='OK', status=200):
    return jsonify({'success': True,  'data': data, 'message': message}), status

def err(message, status=400):
    return jsonify({'success': False, 'data': None, 'message': message}), status


# ── POST /api/devices/<id>/sensor ──────────────────────────────────────────────
@sensors_bp.route('/devices/<int:device_id>/sensor', methods=['POST'])
def post_sensor(device_id):
    """
    ESP32 envia leitura de temperatura e humidade a cada 60 segundos.
    Corpo: { "temperature": 28.5, "humidity": 65.0 }
    """
    device = Device.query.get(device_id)
    if not device:
        return err('Dispositivo não encontrado', 404)

    data = request.get_json(silent=True)
    if not data:
        return err('Corpo da requisição inválido ou vazio')

    temp = data.get('temperature')
    humi = data.get('humidity')

    if temp is None or humi is None:
        return err("Os campos 'temperature' e 'humidity' são obrigatórios")

    try:
        temp = float(temp)
        humi = float(humi)
    except (ValueError, TypeError):
        return err('Valores de temperatura e humidade devem ser numéricos')

    # Validação de intervalo razoável para DHT11
    if not (-40 <= temp <= 80):
        return err('Temperatura fora do intervalo válido (-40 a 80 °C)')
    if not (0 <= humi <= 100):
        return err('Humidade fora do intervalo válido (0-100 %)')

    reading = SensorReading(
        device_id=device_id,
        temperature=round(temp, 2),
        humidity=round(humi, 2),
    )
    db.session.add(reading)

    # Atualizar status do dispositivo como online
    device.status    = 'online'
    device.last_seen = datetime.utcnow()

    db.session.commit()
    return ok(reading.to_dict(), 'Leitura registada com sucesso', 201)


# ── GET /api/devices/<id>/sensor/history ──────────────────────────────────────
@sensors_bp.route('/devices/<int:device_id>/sensor/history', methods=['GET'])
def get_sensor_history(device_id):
    """
    Retorna o histórico de leituras do sensor.
    Query params: limit (padrão 100)
    """
    device = Device.query.get(device_id)
    if not device:
        return err('Dispositivo não encontrado', 404)

    limit = request.args.get('limit', 100, type=int)
    limit = min(max(limit, 1), 1000)  # entre 1 e 1000

    readings = (
        SensorReading.query
        .filter_by(device_id=device_id)
        .order_by(SensorReading.recorded_at.desc())
        .limit(limit)
        .all()
    )

    # Retornar em ordem cronológica (mais antigo primeiro) para gráficos
    return ok([r.to_dict() for r in reversed(readings)])
