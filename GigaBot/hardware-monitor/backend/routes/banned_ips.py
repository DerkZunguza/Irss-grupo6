# routes/banned_ips.py — Gestão de IPs banidos com export automático para nginx
import os
import ipaddress
from datetime import datetime
from flask import Blueprint, request, jsonify

from models import db, BannedIp, Device

banned_ips_bp = Blueprint('banned_ips', __name__)

# Caminho do ficheiro lido pelo nginx (volume partilhado definido no docker-compose)
BLOCKED_IPS_FILE = os.environ.get('BLOCKED_IPS_FILE', '/nginx-conf/blocked_ips.conf')


# ── Helpers ────────────────────────────────────────────────────────────────────

def ok(data=None, message='OK', status=200):
    return jsonify({'success': True,  'data': data, 'message': message}), status

def err(message, status=400):
    return jsonify({'success': False, 'data': None, 'message': message}), status


def validate_ip(ip_str):
    """
    Valida um endereço IP simples ou notação CIDR.
    Retorna True se válido, False caso contrário.
    """
    try:
        ipaddress.ip_network(ip_str, strict=False)
        return True
    except ValueError:
        return False


def export_nginx_conf():
    """
    Gera o ficheiro blocked_ips.conf com todos os IPs activos (não expirados).
    Este ficheiro é lido pelo nginx via `include` e o watcher faz reload automático.
    """
    agora = datetime.utcnow()

    # Buscar apenas bans activos e não expirados
    bans = BannedIp.query.filter_by(active=True).all()
    bans_validos = [b for b in bans if b.expires_at is None or b.expires_at > agora]

    linhas = [
        f'# blocked_ips.conf — Auto-gerado em {agora.isoformat()}Z',
        '# NÃO EDITAR MANUALMENTE — gerido pela API /api/banned-ips',
        '',
    ]

    for ban in bans_validos:
        comentario_parts = []
        if ban.reason:
            comentario_parts.append(f'motivo: {ban.reason}')
        if ban.device:
            comentario_parts.append(f'dispositivo: {ban.device.name}')
        if ban.expires_at:
            comentario_parts.append(f'expira: {ban.expires_at.isoformat()}Z')

        comentario = ' | '.join(comentario_parts)
        linha = f'{ban.ip_address:<45} 1;'
        if comentario:
            linha += f'  # {comentario}'
        linhas.append(linha)

    # Garantir que o directório existe
    os.makedirs(os.path.dirname(BLOCKED_IPS_FILE), exist_ok=True)

    with open(BLOCKED_IPS_FILE, 'w') as f:
        f.write('\n'.join(linhas) + '\n')


# ── GET /api/banned-ips ───────────────────────────────────────────────────────
@banned_ips_bp.route('/banned-ips', methods=['GET'])
def list_banned():
    """Lista todos os registos de ban (activos e inactivos)."""
    device_id  = request.args.get('device_id', type=int)
    only_active = request.args.get('active', 'false').lower() == 'true'

    query = BannedIp.query
    if device_id:
        query = query.filter_by(device_id=device_id)
    if only_active:
        agora = datetime.utcnow()
        query = query.filter(
            BannedIp.active == True,
            db.or_(BannedIp.expires_at == None, BannedIp.expires_at > agora)
        )

    bans = query.order_by(BannedIp.created_at.desc()).all()
    return ok([b.to_dict() for b in bans])


# ── POST /api/banned-ips ──────────────────────────────────────────────────────
@banned_ips_bp.route('/banned-ips', methods=['POST'])
def add_ban():
    """
    Bane um IP ou sub-rede, opcionalmente associado a um dispositivo.
    Corpo: {
      "ip_address": "192.168.1.5",   ← ou "192.168.1.0/24"
      "reason":     "Spam de requests",
      "device_id":  1,               ← opcional
      "expires_at": "2024-12-31T23:59:59"  ← opcional (omitir = permanente)
    }
    """
    data = request.get_json(silent=True)
    if not data:
        return err('Corpo da requisição inválido ou vazio')

    ip_str = (data.get('ip_address') or '').strip()
    if not ip_str:
        return err("O campo 'ip_address' é obrigatório")
    if not validate_ip(ip_str):
        return err(f"Endereço IP inválido: '{ip_str}'. Use IPv4, IPv6 ou notação CIDR.")

    # Normalizar CIDR (ex.: "192.168.1.5/24" → "192.168.1.0/24")
    ip_normalizado = str(ipaddress.ip_network(ip_str, strict=False))

    # Verificar se já existe
    existente = BannedIp.query.filter_by(ip_address=ip_normalizado).first()
    if existente:
        if existente.active:
            return err(f"IP '{ip_normalizado}' já está banido (id={existente.id})", 409)
        # Reactivar ban existente
        existente.active     = True
        existente.reason     = data.get('reason', existente.reason)
        existente.device_id  = data.get('device_id', existente.device_id)
        existente.expires_at = _parse_expires(data.get('expires_at'))
        existente.created_at = datetime.utcnow()
        db.session.commit()
        export_nginx_conf()
        return ok(existente.to_dict(), f"Ban de '{ip_normalizado}' reactivado", 200)

    # Validar device_id se fornecido
    device_id = data.get('device_id')
    if device_id and not Device.query.get(device_id):
        return err(f"Dispositivo com id={device_id} não encontrado", 404)

    ban = BannedIp(
        ip_address=ip_normalizado,
        reason=data.get('reason'),
        device_id=device_id,
        expires_at=_parse_expires(data.get('expires_at')),
    )
    db.session.add(ban)
    db.session.commit()

    # Exportar e o nginx watcher irá fazer reload automaticamente
    export_nginx_conf()

    return ok(ban.to_dict(), f"IP '{ip_normalizado}' banido com sucesso", 201)


# ── DELETE /api/banned-ips/<id> ───────────────────────────────────────────────
@banned_ips_bp.route('/banned-ips/<int:ban_id>', methods=['DELETE'])
def remove_ban(ban_id):
    """Remove (desactiva) um ban. O IP volta a ter acesso."""
    ban = BannedIp.query.get(ban_id)
    if not ban:
        return err('Registo de ban não encontrado', 404)

    ban.active = False
    db.session.commit()
    export_nginx_conf()

    return ok(ban.to_dict(), f"Ban do IP '{ban.ip_address}' removido com sucesso")


# ── POST /api/banned-ips/export ───────────────────────────────────────────────
@banned_ips_bp.route('/banned-ips/export', methods=['POST'])
def force_export():
    """
    Força a regeneração do ficheiro blocked_ips.conf.
    Útil para sincronizar manualmente após alterações directas na BD.
    """
    try:
        export_nginx_conf()
        agora = datetime.utcnow()
        activos = BannedIp.query.filter(
            BannedIp.active == True,
            db.or_(BannedIp.expires_at == None, BannedIp.expires_at > agora)
        ).count()
        return ok({'file': BLOCKED_IPS_FILE, 'active_bans': activos},
                  f'Ficheiro exportado com {activos} ban(s) activo(s)')
    except Exception as e:
        return err(f'Erro ao exportar ficheiro: {str(e)}', 500)


# ── Auxiliar: parse de data de expiração ──────────────────────────────────────
def _parse_expires(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).replace(tzinfo=None)
    except ValueError:
        return None
