from flask import Flask, jsonify, request
from flask_pymongo import PyMongo
from bson.objectid import ObjectId
from bson import json_util
import json
import os
from datetime import datetime

app = Flask(__name__)

app.config["MONGO_URI"] = os.environ.get(
    "MONGO_URI", "mongodb://mongo:27017/projetodb"
)

mongo = PyMongo(app)

def parse_json(data):
    return json.loads(json_util.dumps(data))

# ─── Health Check ────────────────────────────────────────────────
@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "timestamp": datetime.utcnow().isoformat()})

# ─── CRUD de Tarefas ─────────────────────────────────────────────
@app.route("/api/tarefas", methods=["GET"])
def listar_tarefas():
    tarefas = list(mongo.db.tarefas.find())
    return jsonify(parse_json(tarefas)), 200

@app.route("/api/tarefas", methods=["POST"])
def criar_tarefa():
    data = request.get_json()
    if not data or not data.get("titulo"):
        return jsonify({"erro": "Campo 'titulo' é obrigatório"}), 400

    nova = {
        "titulo": data["titulo"],
        "descricao": data.get("descricao", ""),
        "concluida": False,
        "criada_em": datetime.utcnow().isoformat()
    }
    result = mongo.db.tarefas.insert_one(nova)
    nova["_id"] = str(result.inserted_id)
    return jsonify(nova), 201

@app.route("/api/tarefas/<id>", methods=["PUT"])
def atualizar_tarefa(id):
    data = request.get_json()
    mongo.db.tarefas.update_one(
        {"_id": ObjectId(id)},
        {"$set": {
            "titulo": data.get("titulo"),
            "descricao": data.get("descricao", ""),
            "concluida": data.get("concluida", False)
        }}
    )
    tarefa = mongo.db.tarefas.find_one({"_id": ObjectId(id)})
    return jsonify(parse_json(tarefa)), 200

@app.route("/api/tarefas/<id>", methods=["DELETE"])
def deletar_tarefa(id):
    mongo.db.tarefas.delete_one({"_id": ObjectId(id)})
    return jsonify({"mensagem": "Tarefa removida"}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
