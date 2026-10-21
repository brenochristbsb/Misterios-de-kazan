import os
import telebot
from flask import Flask, request

from bot import bot


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
    app = Flask(__name__)

    @app.route("/")
    def home():
        return "🤖 Mistérios de Kazan — Bot está no ar!"

    @app.route("/webhook", methods=["POST"])
    def webhook():
        if request.headers.get("content-type") == "application/json":
            update = telebot.types.Update.de_json(request.get_data().decode("utf-8"))
            bot.process_new_updates([update])
            return "!", 200
        return "Erro", 403

    configurar_webhook()
    return app
