# models.py — Modelos SQLAlchemy para o sistema de monitoramento de hardware
# ESP32-S3 + Arduino Uno com SIM900 GPRS Shield
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


class Contact(db.Model):
    """
    Contacto associado a um número de telefone (WhatsApp ou SMS).
    Liga mensagens a um cliente/dispositivo específico.
    """
    __tablename__ = 'contacts'

    id           = db.Column(db.Integer, primary_key=True)
    phone_number = db.Column(db.String(30), nullable=False, unique=True)
    name         = db.Column(db.String(100), nullable=True)
    notes        = db.Column(db.Text,        nullable=True)
    device_id    = db.Column(db.Integer, db.ForeignKey('devices.id'), nullable=True)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at   = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    messages = db.relationship('SmsMessage', backref='contact', lazy='dynamic')

    def to_dict(self):
        return {
            'id':           self.id,
            'phone_number': self.phone_number,
            'name':         self.name,
            'notes':        self.notes,
            'device_id':    self.device_id,
            'device_name':  self.device.name if self.device else None,
            'created_at':   self.created_at.isoformat() + 'Z' if self.created_at else None,
            'updated_at':   self.updated_at.isoformat() + 'Z' if self.updated_at else None,
        }


class Device(db.Model):
    """Representa um dispositivo registado no sistema (ESP32-S3 / Arduino)."""
    __tablename__ = 'devices'

    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(100), nullable=False)
    type       = db.Column(db.String(50),  nullable=False)
    ip_address = db.Column(db.String(50),  nullable=True)
    status     = db.Column(db.String(20),  default='offline')
    last_seen  = db.Column(db.DateTime,    default=datetime.utcnow)
    created_at = db.Column(db.DateTime,    default=datetime.utcnow)

    sensor_readings = db.relationship('SensorReading', backref='device', lazy='dynamic', cascade='all, delete-orphan')
    commands        = db.relationship('DeviceCommand',  backref='device', lazy='dynamic', cascade='all, delete-orphan')
    sms_messages    = db.relationship('SmsMessage',     backref='device', lazy='dynamic', cascade='all, delete-orphan')
    banned_ips      = db.relationship('BannedIp',       backref='device', lazy='dynamic', cascade='all, delete-orphan')
    contacts        = db.relationship('Contact',        backref='device', lazy='dynamic')

    def to_dict(self):
        return {
            'id':         self.id,
            'name':       self.name,
            'type':       self.type,
            'ip_address': self.ip_address,
            'status':     self.status,
            'last_seen':  self.last_seen.isoformat() + 'Z'  if self.last_seen  else None,
            'created_at': self.created_at.isoformat() + 'Z' if self.created_at else None,
        }


class SensorReading(db.Model):
    """Leitura do sensor DHT11 (temperatura e humidade)."""
    __tablename__ = 'sensor_readings'

    id          = db.Column(db.Integer, primary_key=True)
    device_id   = db.Column(db.Integer, db.ForeignKey('devices.id'), nullable=False)
    temperature = db.Column(db.Float,   nullable=False)
    humidity    = db.Column(db.Float,   nullable=False)
    recorded_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id':          self.id,
            'device_id':   self.device_id,
            'temperature': self.temperature,
            'humidity':    self.humidity,
            'recorded_at': self.recorded_at.isoformat() + 'Z' if self.recorded_at else None,
            'created_at':  self.created_at.isoformat() + 'Z'  if self.created_at  else None,
        }


class DeviceCommand(db.Model):
    """Comando de controlo de LED ou relé enfileirado para o ESP32."""
    __tablename__ = 'device_commands'

    id         = db.Column(db.Integer, primary_key=True)
    device_id  = db.Column(db.Integer, db.ForeignKey('devices.id'), nullable=False)
    command    = db.Column(db.String(10), nullable=False)
    target     = db.Column(db.String(20), nullable=False)
    executed   = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id':         self.id,
            'device_id':  self.device_id,
            'command':    self.command,
            'target':     self.target,
            'executed':   self.executed,
            'created_at': self.created_at.isoformat() + 'Z' if self.created_at else None,
        }


class SmsMessage(db.Model):
    """
    Mensagem de texto recebida ou enviada.
    Canal: SMS (via SIM900) ou WHATSAPP (via Baileys).
    Associada a um contacto e opcionalmente a um dispositivo.
    """
    __tablename__ = 'sms_messages'

    id           = db.Column(db.Integer, primary_key=True)
    device_id    = db.Column(db.Integer, db.ForeignKey('devices.id'), nullable=True)
    contact_id   = db.Column(db.Integer, db.ForeignKey('contacts.id'), nullable=True)
    channel      = db.Column(db.String(10), nullable=False, server_default='SMS')  # SMS / WHATSAPP
    direction    = db.Column(db.String(3),  nullable=False)   # IN / OUT
    phone_number = db.Column(db.String(30), nullable=False)
    message_body = db.Column(db.Text,       nullable=False)
    media_url    = db.Column(db.Text,       nullable=True)    # URL/base64 de imagem (comprovativo)
    media_type   = db.Column(db.String(20), nullable=True)    # image, document, audio...
    received_at  = db.Column(db.DateTime,   nullable=True)
    synced_at    = db.Column(db.DateTime,   default=datetime.utcnow)
    sent         = db.Column(db.Boolean,    default=False)
    created_at   = db.Column(db.DateTime,   default=datetime.utcnow)

    def to_dict(self):
        return {
            'id':           self.id,
            'device_id':    self.device_id,
            'contact_id':   self.contact_id,
            'contact_name': self.contact.name if self.contact else None,
            'channel':      self.channel,
            'direction':    self.direction,
            'phone_number': self.phone_number,
            'message_body': self.message_body,
            'media_url':    self.media_url,
            'media_type':   self.media_type,
            'received_at':  self.received_at.isoformat() + 'Z'  if self.received_at  else None,
            'synced_at':    self.synced_at.isoformat() + 'Z'    if self.synced_at    else None,
            'sent':         self.sent,
            'created_at':   self.created_at.isoformat() + 'Z'   if self.created_at   else None,
        }


class BannedIp(db.Model):
    """IP ou sub-rede banida, opcionalmente associada a um dispositivo."""
    __tablename__ = 'banned_ips'

    id         = db.Column(db.Integer, primary_key=True)
    ip_address = db.Column(db.String(50), nullable=False, unique=True)
    reason     = db.Column(db.String(255), nullable=True)
    device_id  = db.Column(db.Integer, db.ForeignKey('devices.id'), nullable=True)
    active     = db.Column(db.Boolean, default=True)
    expires_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def is_expired(self):
        if self.expires_at is None:
            return False
        return datetime.utcnow() > self.expires_at

    def to_dict(self):
        return {
            'id':          self.id,
            'ip_address':  self.ip_address,
            'reason':      self.reason,
            'device_id':   self.device_id,
            'device_name': self.device.name if self.device else None,
            'active':      self.active and not self.is_expired(),
            'expires_at':  self.expires_at.isoformat() + 'Z' if self.expires_at else None,
            'created_at':  self.created_at.isoformat() + 'Z' if self.created_at else None,
        }
