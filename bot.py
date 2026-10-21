import telebot
import os
from flask import Flask, request

BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
bot = telebot.TeleBot(BOT_TOKEN)

app = Flask(__name__)

@bot.message_handler(commands=['start'])
def cmd_start(message):
    bot.reply_to(message, "🌙 Bem-vindo aos Mistérios de Kazan!\nDigite /ajuda para ver os comandos disponíveis.")

@bot.message_handler(commands=['ajuda'])
def cmd_ajuda(message):
    bot.reply_to(message, "📜 Comandos disponíveis:\n/start — Iniciar\n/ajuda — Ver ajuda")

@app.route('/webhook', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return '!', 200
    return 'Erro', 403

@app.route('/')
def home():
    return "🤖 Bot dos Mistérios de Kazan está rodando!"

if __name__ == "__main__":
    app.run()
