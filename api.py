import os
from dataclasses import asdict

import telebot
from flask import Flask, request, jsonify, send_from_directory, abort

from bot import bot
import sistema_historias as hist

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")


def configurar_webhook():
    """Registra o webhook no Telegram usando a URL pública do Render."""
    base = os.environ.get("WEBHOOK_URL") or os.environ.get("RENDER_EXTERNAL_URL")
    if not base:
        print("WEBHOOK_URL/RENDER_EXTERNAL_URL não definida; webhook não configurado.")
        return
    try:
        bot.remove_webhook()
        bot.set_webhook(url=base.rstrip("/") + "/webhook")
        print("Webhook configurado em", base)
    except Exception as e:
        print("Falha ao configurar webhook:", e)


def create_app():
    app = Flask(__name__, static_folder=STATIC_DIR)

    # --- Mini App (o jogo) ---------------------------------------------
    @app.route("/")
    @app.route("/jogo")
    def jogo():
        return send_from_directory(STATIC_DIR, "jogo.html")

    @app.route("/saude")
    def saude():
        return "🤖 Mistérios de Kazan — Bot está no ar!"

    # --- API do jogo ---------------------------------------------------
    @app.route("/api/sagas")
    def api_sagas():
        return jsonify(hist.listar_sagas())

    @app.route("/api/saga/<saga_id>/inicio")
    def api_inicio(saga_id):
        cap = hist.obter_inicio_saga(saga_id)
        if not cap:
            abort(404)
        return jsonify(asdict(cap))

    @app.route("/api/saga/<saga_id>/capitulo/<cap_id>")
    def api_capitulo(saga_id, cap_id):
        cap = hist.buscar_capitulo(saga_id, cap_id)
        if not cap:
            abort(404)
        return jsonify(asdict(cap))

    # --- Webhook do Telegram -------------------------------------------
    @app.route("/webhook", methods=["POST"])
    def webhook():
        if request.headers.get("content-type") == "application/json":
            update = telebot.types.Update.de_json(request.get_data().decode("utf-8"))
            bot.process_new_updates([update])
            return "!", 200
        return "Erro", 403

    configurar_webhook()
    return app
