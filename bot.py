import telebot
import os

BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
bot = telebot.TeleBot(BOT_TOKEN)

@bot.message_handler(commands=['start'])
def cmd_start(message):
    bot.reply_to(message, "🌙 Bem-vindo aos Mistérios de Kazan!\nDigite /ajuda para ver os comandos disponíveis.")

@bot.message_handler(commands=['ajuda'])
def cmd_ajuda(message):
    bot.reply_to(message, "📜 Comandos disponíveis:\n/start — Iniciar\n/ajuda — Ver ajuda")

if __name__ == "__main__":
    print("🤖 Bot rodando...")
    bot.infinity_polling()
