IDIOMAS = ["pt", "en", "es"]

TEXTOS = {
    "pt": {
        "bem_vindo": "✨ Bem-vindo, {nome}! Sua jornada começa agora.",
        "seu_perfil": "👤 Seu Perfil",
        "explorar": "🗺️ Explorar",
        "combate": "⚔️ Combate",
        "hospital": "🏥 Descansar",
        "missao_diaria": "📜 Missão Diária",
        "convites": "🤝 Convite",
        "historias": "📖 Histórias",
        "codigo_convite": "Seu código: {codigo}",
        "convite_resgatado": "🎉 Convite resgatado! Recompensas enviadas!",
        "erro_generico": "Ocorreu um erro. Tente novamente."
    },
    "en": {
        "bem_vindo": "✨ Welcome, {nome}! Your journey begins now.",
        "seu_perfil": "👤 Your Profile",
        "explorar": "🗺️ Explore",
        "combate": "⚔️ Combat",
        "hospital": "🏥 Rest",
        "missao_diaria": "📜 Daily Mission",
        "convites": "🤝 Invite",
        "historias": "📖 Stories",
        "codigo_convite": "Your code: {codigo}",
        "convite_resgatado": "🎉 Invite redeemed! Rewards sent!",
        "erro_generico": "An error occurred. Please try again."
    },
    "es": {
        "bem_vindo": "✨ Bienvenido, {nome}! Tu viaje comienza ahora.",
        "seu_perfil": "👤 Tu Perfil",
        "explorar": "🗺️ Explorar",
        "combate": "⚔️ Combate",
        "hospital": "🏥 Descansar",
        "missao_diaria": "📜 Misión Diaria",
        "convites": "🤝 Invitación",
        "historias": "📖 Historias",
        "codigo_convite": "Tu código: {codigo}",
        "convite_resgatado": "🎉 ¡Código canjeado! ¡Recompensas enviadas!",
        "erro_generico": "Ocurrió un error. Intenta de nuevo."
    }
}

def _(chave: str, lang="pt", **kwargs):
    if lang not in TEXTOS:
        lang = "pt"
    texto = TEXTOS[lang].get(chave, f"[{chave}]")
    return texto.format(**kwargs) if kwargs else texto
