import telebot
import os
from flask import Flask, request

# Pegue o token do BotFather e cole aqui
BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', 'COLOQUE_SEU_TOKEN_AQUI')

bot = telebot.TeleBot(BOT_TOKEN)

app = Flask(__name__)

# Comandos do bot
@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "Bem-vindo aos Misterérios de Kazan! 🌙\nUse /ajuda para ver os comandos disponíveis.")

@bot.message_handler(commands=['ajuda'])
def ajuda(message):
    bot.reply_to(message, "Comandos disponíveis:\n/start - Iniciar\n/ajuda - Ver ajuda")

# Webhook para receber mensagens do Telegram
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
    return "Bot dos Mistérios de Kazan rodando! 🤖"

if __name__ == "__main__":
    app.run()
