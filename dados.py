"""Dados iniciais do Shadow Clan RPG (GDD v1.2). Nomes de clãs são 💡 provisórios (seção 30)."""

ALDEIAS = [
    ("Hinokawa", "Aldeia das Brasas", "Brasa Eterna"),
    ("Kazeyama", "Aldeia do Vento Alto", "Pena Primeira"),
    ("Kaminari", "Aldeia da Tempestade", "Coração do Trovão"),
    ("Ishigaki", "Aldeia do Muro de Pedra", "Olho de Rocha"),
    ("Shizuku", "Aldeia do Lago Espelhado", "Lágrima Primeira"),
]

CLAS = {
    "Hinokawa": [("Kagari", "sinais de fogo"), ("Kanatsuchi", "forjadores"), ("Hizume", "duelistas de brasa")],
    "Kazeyama": [("Tsubame", "mensageiros"), ("Ito-no-Kasumi", "cordas e pontes"), ("Hayabusa", "batedores")],
    "Kaminari": [("Inazuma-ya", "inventores"), ("Hikari", "duelistas"), ("Tōrō", "guardiões das torres")],
    "Ishigaki": [("Iwaoto", "mineradores"), ("Tategami", "guardiões do muro"), ("Shichidan", "exploradores do sétimo túnel")],
    "Shizuku": [("Mizukagami", "ilusionistas"), ("Kusushi", "médicos"), ("Namida", "diplomatas")],
}

# (chave, nome, slot, raridade, classe_restrita, dano, preco_npc, empilhavel)
_ARMAS = {
    "Taijutsu": ("manopla", "Manopla"), "Ninjutsu": ("katana", "Katana"),
    "Genjutsu": ("leque", "Leque"), "Marionetista": ("nucleo", "Núcleo de Madeira"),
    "Kunoichi": ("bisturi", "Bisturi de Chakra"),
}
_TIERS = [("comum", 15, 30, "de Treino"), ("incomum", 22, 120, "do Clã"), ("raro", 32, 500, "da Aldeia")]

ITENS = []
for classe, (chave, nome) in _ARMAS.items():
    for raridade, dano, preco, suf in _TIERS:
        ITENS.append((f"{chave}_{raridade}", f"{nome} {suf}", "arma", raridade, classe, dano, preco, 0))
ITENS += [
    ("tunica_treino", "Túnica de Treino", "peitoral", "comum", None, 0, 20, 0),
    ("bandana_aldeia", "Bandana da Aldeia", "cabeca", "comum", None, 0, 20, 0),
    ("amuleto_sorte", "Amuleto da Sorte", "amuleto", "incomum", None, 0, 90, 0),
    ("fibra", "Fibra Resistente", None, "comum", None, 0, 2, 1),
    ("minerio", "Minério Bruto", None, "comum", None, 0, 3, 1),
    ("essencia_rara", "Essência Rara", None, "raro", None, 0, 40, 1),
    ("fragmento_mapa", "Fragmento de Mapa", None, "comum", None, 0, 0, 1),
]
BONUS = {"amuleto_sorte": '{"DEX": 3}', "tunica_treino": '{"VIG": 2}', "bandana_aldeia": '{"AGI": 1}'}

MATERIAIS_BASICOS = ["fibra", "minerio"]
MATERIAIS_RAROS = ["essencia_rara"]

# Questionário (GDD 4.1): cada resposta soma 1 ponto a um elemento.
QUESTIONARIO = [
    ("Diante de um rival muito mais forte, você:",
     {"Fogo": "ataca primeiro", "Vento": "se desvia e espera", "Relâmpago": "reage no tempo exato",
      "Terra": "resiste firme", "Água": "se adapta e contorna"}),
    ("Qual qualidade te define?",
     {"Fogo": "Paixão", "Vento": "Liberdade", "Relâmpago": "Rapidez", "Terra": "Lealdade", "Água": "Calma"}),
    ("Em equipe, você é:",
     {"Fogo": "o líder que inspira", "Vento": "o explorador", "Relâmpago": "o estrategista veloz",
      "Terra": "o protetor", "Água": "o apoio"}),
    ("Seu lugar favorito:",
     {"Fogo": "um vulcão", "Vento": "o topo de uma montanha ventosa", "Relâmpago": "uma tempestade",
      "Terra": "uma caverna ou floresta", "Água": "um rio ou cachoeira"}),
    ("Sua maior fraqueza:",
     {"Fogo": "impulsividade", "Vento": "inconstância", "Relâmpago": "impaciência",
      "Terra": "teimosia", "Água": "indecisão"}),
]

# Atributos iniciais por classe (GDD 3: principal por classe)
INICIAL = {
    "Taijutsu":     dict(str=15, agi=9, int=1, dex=5, vig=10),
    "Ninjutsu":     dict(str=1, agi=6, int=15, dex=8, vig=10),
    "Genjutsu":     dict(str=1, agi=8, int=15, dex=8, vig=8),
    "Marionetista": dict(str=1, agi=6, int=14, dex=8, vig=11),
    "Kunoichi":     dict(str=1, agi=8, int=15, dex=8, vig=8),
}

ANUNCIO_BOSS = {
    "Pesada": "ergue a arma com as duas mãos…",
    "Rápida": "ajusta a postura, pronto para uma sequência veloz…",
    "Perfurante": "afia o olhar e mira uma brecha na sua guarda…",
    "Técnica": "reúne chakra e começa a moldar um selo…",
}
