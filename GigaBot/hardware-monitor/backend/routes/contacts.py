# routes/contacts.py — Gestão de contactos WhatsApp/SMS
from flask import Blueprint, request, jsonify
from datetime import datetime

from models import db, Contact, Device, SmsMessage
import cache as redis_cache

contacts_bp = Blueprint('contacts', __name__)


def ok(data=None, message='OK', status=200):
    return jsonify({'success': True,  'data': data, 'message': message}), status

def err(message, status=400):
    return jsonify({'success': False, 'data': None, 'message': message}), status


# ── GET /api/contacts ─────────────────────────────────────────────────────────
@contacts_bp.route('/contacts', methods=['GET'])
def list_contacts():
    device_id = request.args.get('device_id', type=int)
    query = Contact.query
    if device_id:
        query = query.filter_by(device_id=device_id)
    contacts = query.order_by(Contact.name).all()

    data = []
    for c in contacts:
        d = c.to_dict()
        d['unread'] = redis_cache.get_unread_count(c.phone_number)
        data.append(d)
    return ok(data)


# ── POST /api/contacts ────────────────────────────────────────────────────────
@contacts_bp.route('/contacts', methods=['POST'])
def create_contact():
    data = request.get_json(silent=True)
    if not data:
        return err('Corpo inválido')

    phone = (data.get('phone_number') or '').strip().replace(' ', '')
    if not phone:
        return err("O campo 'phone_number' é obrigatório")

    if Contact.query.filter_by(phone_number=phone).first():
        return err(f"Contacto com número '{phone}' já existe", 409)

    device_id = data.get('device_id')
    if device_id and not Device.query.get(device_id):
        return err(f"Dispositivo {device_id} não encontrado", 404)

    contact = Contact(
        phone_number=phone,
        name=data.get('name'),
        notes=data.get('notes'),
        device_id=device_id,
    )
    db.session.add(contact)
    db.session.commit()
    redis_cache.cache_contact(phone, contact.to_dict())
    return ok(contact.to_dict(), 'Contacto criado', 201)


# ── GET /api/contacts/<id> ────────────────────────────────────────────────────
@contacts_bp.route('/contacts/<int:contact_id>', methods=['GET'])
def get_contact(contact_id):
    contact = Contact.query.get(contact_id)
    if not contact:
        return err('Contacto não encontrado', 404)
    d = contact.to_dict()
    d['unread'] = redis_cache.get_unread_count(contact.phone_number)
    return ok(d)


# ── PATCH /api/contacts/<id> ──────────────────────────────────────────────────
@contacts_bp.route('/contacts/<int:contact_id>', methods=['PATCH'])
def update_contact(contact_id):
    contact = Contact.query.get(contact_id)
    if not contact:
        return err('Contacto não encontrado', 404)

    data = request.get_json(silent=True) or {}
    if 'name'      in data: contact.name      = data['name']
    if 'notes'     in data: contact.notes     = data['notes']
    if 'device_id' in data:
        if data['device_id'] and not Device.query.get(data['device_id']):
            return err(f"Dispositivo {data['device_id']} não encontrado", 404)
        contact.device_id = data['device_id']

    contact.updated_at = datetime.utcnow()
    db.session.commit()
    redis_cache.invalidate_contact(contact.phone_number)
    return ok(contact.to_dict(), 'Contacto actualizado')


# ── DELETE /api/contacts/<id> ─────────────────────────────────────────────────
@contacts_bp.route('/contacts/<int:contact_id>', methods=['DELETE'])
def delete_contact(contact_id):
    contact = Contact.query.get(contact_id)
    if not contact:
        return err('Contacto não encontrado', 404)
    redis_cache.invalidate_contact(contact.phone_number)
    db.session.delete(contact)
    db.session.commit()
    return ok(None, 'Contacto removido')


# ── GET /api/contacts/<id>/messages ───────────────────────────────────────────
@contacts_bp.route('/contacts/<int:contact_id>/messages', methods=['GET'])
def contact_messages(contact_id):
    """
    Retorna mensagens do contacto.
    Tenta Redis primeiro (mensagens recentes), cai para BD se Redis vazio.
    """
    contact = Contact.query.get(contact_id)
    if not contact:
        return err('Contacto não encontrado', 404)

    limit   = min(request.args.get('limit', 50, type=int), 200)
    channel = request.args.get('channel')  # SMS / WHATSAPP / None (todos)

    # Tentar cache Redis primeiro para mensagens WhatsApp
    if not channel or channel == 'WHATSAPP':
        cached = redis_cache.get_recent_messages(contact.phone_number, limit)
        if cached:
            # Zerar não lidas ao abrir conversa
            redis_cache.clear_unread(contact.phone_number)
            return ok({'source': 'cache', 'messages': cached})

    # Fallback: buscar na BD
    query = SmsMessage.query.filter_by(contact_id=contact_id)
    if channel:
        query = query.filter_by(channel=channel.upper())
    messages = query.order_by(SmsMessage.received_at.desc()).limit(limit).all()

    redis_cache.clear_unread(contact.phone_number)
    return ok({'source': 'database', 'messages': [m.to_dict() for m in messages]})


# ── GET /api/contacts/receipts/pending ───────────────────────────────────────
@contacts_bp.route('/contacts/receipts/pending', methods=['GET'])
def pending_receipts():
    """Retorna comprovativos pendentes de processamento (da fila Redis)."""
    count = redis_cache.count_pending_receipts()
    items = redis_cache.pop_pending_receipts(count=min(count, 50))
    return ok({'count': len(items), 'receipts': items})
