# app.py — Aplicação Flask principal para controle e monitoramento de hardware
# ESP32-S3 + Arduino Uno com SIM900 GPRS Shield
import os
from flask import Flask, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

from models import db
from routes.devices    import devices_bp
from routes.sensors    import sensors_bp
from routes.commands   import commands_bp
from routes.sms        import sms_bp
from routes.banned_ips import banned_ips_bp
from routes.whatsapp   import whatsapp_bp
from routes.contacts   import contacts_bp
import cache as redis_cache

# Carregar variáveis de ambiente do ficheiro .env
load_dotenv()


def create_app():
    """Fábrica de aplicação Flask."""
    app = Flask(__name__)

    # ── Configuração do banco de dados PostgreSQL ──────────────────────────────
    db_user = os.environ.get('DB_USER',     'hardware')
    db_pass = os.environ.get('DB_PASSWORD', 'hardware123')
    db_host = os.environ.get('DB_HOST',     'postgres')
    db_port = os.environ.get('DB_PORT',     '5432')
    db_name = os.environ.get('DB_NAME',     'hardware_monitor')

    app.config['SQLALCHEMY_DATABASE_URI'] = (
        f'postgresql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}'
    )
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # ── Configuração do Redis ──────────────────────────────────────────────────
    app.config['REDIS_HOST'] = os.environ.get('REDIS_HOST', 'redis-wa')
    app.config['REDIS_PORT'] = int(os.environ.get('REDIS_PORT', 6379))
    app.config['REDIS_DB']   = int(os.environ.get('REDIS_DB',   1))

    # ── Inicializar extensões ──────────────────────────────────────────────────
    db.init_app(app)
    CORS(app, resources={r'/api/*': {'origins': '*'}})
    redis_cache.init_cache(app)

    # ── Registar blueprints ────────────────────────────────────────────────────
    app.register_blueprint(devices_bp,    url_prefix='/api')
    app.register_blueprint(sensors_bp,    url_prefix='/api')
    app.register_blueprint(commands_bp,   url_prefix='/api')
    app.register_blueprint(sms_bp,        url_prefix='/api')
    app.register_blueprint(banned_ips_bp, url_prefix='/api')
    app.register_blueprint(whatsapp_bp,   url_prefix='/api')
    app.register_blueprint(contacts_bp,   url_prefix='/api')

    # ── Criar tabelas se não existirem ─────────────────────────────────────────
    with app.app_context():
        db.create_all()

    # ── Rota de saúde do serviço ───────────────────────────────────────────────
    @app.route('/health')
    def health():
        return jsonify({'success': True, 'data': None, 'message': 'API operacional'})

    # ── Tratamento global de erros ─────────────────────────────────────────────
    @app.errorhandler(404)
    def not_found(e):
        return jsonify({'success': False, 'data': None, 'message': 'Rota não encontrada'}), 404

    @app.errorhandler(405)
    def method_not_allowed(e):
        return jsonify({'success': False, 'data': None, 'message': 'Método não permitido'}), 405

    @app.errorhandler(500)
    def internal_error(e):
        db.session.rollback()
        return jsonify({'success': False, 'data': None, 'message': 'Erro interno do servidor'}), 500

    return app


app = create_app()

if __name__ == '__main__':
    # Modo de desenvolvimento — não usar em produção
    app.run(host='0.0.0.0', port=5000, debug=True)
