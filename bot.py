import telebot

BOT_TOKEN = "8680004401:AAFoAgNIA8ZN7DuxMNRZppIga-gXVHfPt_M"
bot = telebot.TeleBot(BOT_TOKEN)

@bot.message_handler(commands=['start'])
def start(msg):
    bot.reply_to(msg, "🌙 Bem-vindo aos Mistérios de Kazan!")

@bot.message_handler(commands=['ajuda'])
def ajuda(msg):
    bot.reply_to(msg, "Comandos: /start e /ajuda")

print("🤖 Rodando... Mantenha esta janela aberta!")
bot.infinity_polling()
