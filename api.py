import sqlite3
from flask import Flask, jsonify, request
from config import config
from sistema_convites import obter_codigo_convite, resgatar_convite, contar_convites
from sistema_historias import listar_sagas, obter_inicio_saga, buscar_capitulo, verificar_requisito, salvar_progresso, obter_progresso

def conectar():
    return sqlite3.connect(config.DB_PATH)

def init_db():
    import os
    if not os.path.exists(config.DB_PATH):
        with conectar() as conn:
            with open('schema.sql', 'r', encoding='utf-8') as f:
                conn.executescript(f.read())
        print("✅ Banco criado!")

def create_app():
    app = Flask(__name__)
    init_db()
    
    @app.errorhandler(404)
    def not_found(e):
        return {"erro": "Não encontrado", "codigo": 404}, 404
    
    @app.errorhandler(500)
    def server_error(e):
        return {"erro": "Erro interno", "codigo": 500}, 500
    
    @app.route("/")
    def boas_vindas():
        return jsonify({
            "jogo": "Mistérios de Kazan",
            "versao": "0.1.0",
            "status": "online"
        })
    
    @app.route("/api/convites/meu", methods=["GET"])
    def meu_convite():
        uid = 1
        with conectar() as db:
            codigo = obter_codigo_convite(db, uid)
            total = contar_convites(db, uid)
            return jsonify({"codigo": codigo, "convidados": total})
    
    @app.route("/api/convites/usar", methods=["POST"])
    def usar_convite():
        uid = 1
        dados = request.get_json() or {}
        codigo = dados.get("codigo", "")
        if not codigo:
            return {"erro": "Informe o código"}, 400
        with conectar() as db:
            res = resgatar_convite(db, uid, codigo)
            return jsonify(res)
    
    @app.route("/api/historias/sagas", methods=["GET"])
    def sagas_lista():
        return jsonify({"sagas": listar_sagas()})
    
    @app.route("/api/historias/iniciar", methods=["POST"])
    def iniciar_saga():
        uid = 1
        dados = request.get_json() or {}
        saga_id = dados.get("saga_id")
        cap = obter_inicio_saga(saga_id)
        if not cap:
            return {"erro": "Saga não encontrada"}, 404
        with conectar() as db:
            salvar_progresso(db, uid, saga_id, cap.id)
        return jsonify({
            "saga_id": saga_id,
            "capitulo": cap.id,
            "titulo": cap.titulo,
            "texto": cap.texto
        })
    
    @app.route("/api/historias/capitulo", methods=["GET"])
    def ler_capitulo():
        uid = 1
        saga_id = request.args.get("saga")
        cap_id = request.args.get("capitulo")
        if not saga_id:
            return {"erro": "Informe a saga"}, 400
        if not cap_id:
            with conectar() as db:
                prog = obter_progresso(db, uid, saga_id)
                cap_id = prog["capitulo_atual"]
                if not cap_id:
                    return {"erro": "Saga não iniciada"}, 404
        cap = buscar_capitulo(saga_id, cap_id)
        if not cap:
            return {"erro": "Capítulo não encontrado"}, 404
        with conectar() as db:
            jogador_db = db.execute("SELECT nivel FROM players WHERE id = ?", (uid,)).fetchone()
        jogador = {"nivel": jogador_db[0] if jogador_db else 1}
        if not verificar_requisito(cap, jogador):
            return {
                "titulo": cap.titulo,
                "bloqueado": True,
                "requisito": cap.requisito,
                "mensagem": "Você ainda não atende aos requisitos."
            }, 200
        return jsonify({
            "titulo": cap.titulo,
            "texto": cap.texto,
            " "proximo": cap.proximo,
            "recompensas": cap.recompensas
        })
    
    return app
