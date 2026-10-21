import os
import telebot
from telebot import types

TOKEN = os.environ["BOT_TOKEN"]
bot = telebot.TeleBot(TOKEN, threaded=False)

# Cada cena: texto + opções (rótulo, id da próxima cena).
# O id da cena vai no callback_data, então não precisa guardar estado.
HISTORIA = {
    "inicio": {
        "texto": "🌫️ Você chega à cidade de Kazan ao anoitecer. Um estranho "
                 "lhe entrega um bilhete: \"Encontre a verdade na velha biblioteca.\"",
        "opcoes": [("📚 Ir à biblioteca", "biblioteca"),
                   ("🍺 Entrar na taverna", "taverna")],
    },
    "biblioteca": {
        "texto": "📚 A biblioteca está vazia. Entre as estantes, você vê uma "
                 "porta secreta entreaberta e uma luz tremeluzindo.",
        "opcoes": [("🚪 Abrir a porta secreta", "porta"),
                   ("↩️ Voltar", "inicio")],
    },
    "taverna": {
        "texto": "🍺 Na taverna, um velho sussurra: \"Cuidado com o relojoeiro. "
                 "Ele sabe o que aconteceu com os desaparecidos.\"",
        "opcoes": [("🕰️ Procurar o relojoeiro", "relojoeiro"),
                   ("↩️ Voltar", "inicio")],
    },
    "porta": {
        "texto": "🔦 Atrás da porta há uma passagem que leva a um porão cheio "
                 "de mapas antigos. Um deles marca a oficina do relojoeiro.",
        "opcoes": [("🕰️ Seguir o mapa", "relojoeiro")],
    },
    "relojoeiro": {
        "texto": "🕰️ O relojoeiro sorri: \"Você chegou longe. Os desaparecidos "
                 "estão presos no tempo... e você será o próximo.\"\n\n"
                 "✨ Fim do primeiro capítulo!",
        "opcoes": [("🔁 Jogar de novo", "inicio")],
    },
}


def teclado(opcoes):
    kb = types.InlineKeyboardMarkup()
    for rotulo, destino in opcoes:
        kb.add(types.InlineKeyboardButton(rotulo, callback_data=destino))
    return kb


@bot.message_handler(commands=["start", "jogar"])
def comando_start(msg):
    cena = HISTORIA["inicio"]
    bot.send_message(
        msg.chat.id,
        "🎮 *Mistérios de Kazan*\n\n" + cena["texto"],
        reply_markup=teclado(cena["opcoes"]),
        parse_mode="Markdown",
    )


@bot.callback_query_handler(func=lambda call: True)
def escolha(call):
    cena = HISTORIA.get(call.data)
    bot.answer_callback_query(call.id)
    if not cena:
        return
    bot.edit_message_text(
        cena["texto"],
        call.message.chat.id,
        call.message.message_id,
        reply_markup=teclado(cena["opcoes"]),
    )


@bot.message_handler(func=lambda m: True)
def outros(msg):
    bot.reply_to(msg, "Envie /start para começar a aventura. 🗡️")
