import os
import telebot
from telebot import types

TOKEN = os.environ["BOT_TOKEN"]
bot = telebot.TeleBot(TOKEN, threaded=False)


def url_do_jogo():
    base = os.environ.get("WEBHOOK_URL") or os.environ.get("RENDER_EXTERNAL_URL")
    return base.rstrip("/") + "/jogo" if base else None


@bot.message_handler(commands=["start", "jogar"])
def comando_start(msg):
    texto = ("🌙 *Mistérios de Kazan*\n\n"
             "As portas da Grande Academia se abrem para você. "
             "Toque no botão abaixo para começar sua jornada.")
    kb = None
    url = url_do_jogo()
    if url:
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🌙 Jogar Kazan", web_app=types.WebAppInfo(url=url)))
    bot.send_message(msg.chat.id, texto, reply_markup=kb, parse_mode="Markdown")


@bot.message_handler(func=lambda m: True)
def outros(msg):
    bot.reply_to(msg, "Envie /start para abrir o jogo. 🗡️")
