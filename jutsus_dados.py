"""Elementos, vantagens e catálogo de jutsus com nome. Só dados, sem imports do jogo.

Ciclo de vantagens (a chave vence o valor): Fogo > Vento > Raio > Terra > Água > Fogo.
Marcos de escolha: níveis 2, 12, 22, 32 e 42, com duas opções por marco.
"""

ELEMENTOS = {"fogo": ("Fogo", "🔥"), "agua": ("Água", "💧"), "raio": ("Raio", "⚡"), "terra": ("Terra", "🪨"), "vento": ("Vento", "🌪️")}
VANTAGEM = {"fogo": "vento", "vento": "raio", "raio": "terra", "terra": "agua", "agua": "fogo"}
FRACO_CONTRA = {v: k for k, v in VANTAGEM.items()}
MARCOS = [2, 12, 22, 32, 42]
PODER_NIN, PODER_TAI, PODER_GEN = [1.3, 1.6, 1.95, 2.35, 2.8], [1.2, 1.45, 1.75, 2.1, 2.5], [1.1, 1.35, 1.65, 2.0, 2.4]
CUSTO = [1.3, 1.6, 2.0, 2.4, 2.8]  # múltiplo do custo de chakra de uma técnica comum

NINJUTSU = {
    "fogo": ["Brasa Saltitante", "Língua de Dragão Rubro", "Muralha Ardente", "Tempestade de Cinzas", "Fênix Solar"],
    "agua": ["Gota Cortante", "Onda Rastejante", "Dragão de Maré", "Prisão do Abismo", "Maremoto Celeste"],
    "raio": ["Faísca Serpente", "Lança Trovejante", "Corrente Relâmpago", "Grilhão Elétrico", "Trovão do Juízo"],
    "terra": ["Estaca de Pedra", "Muro Rochoso", "Punho de Granito", "Abalo Sísmico", "Colosso de Montanha"],
    "vento": ["Lâmina do Zéfiro", "Rajada Cortante", "Redemoinho Veloz", "Ciclone Prateado", "Tormenta Divina"],
}
TAIJUTSU = ["Sequência Tigre", "Quebra-Muralha", "Passo Fantasma", "Dança das Lâminas Cegas", "Punho do Silêncio"]
GENJUTSU = ["Véu de Sombras", "Espelho Enganoso", "Pesadelo Desperto", "Teia de Ilusão", "Mundo Sem Lua"]
BASICO = {"fisico": "Punho Firme", "genjutsu": "Ilusão Sombria",
          "ninjutsu": {"fogo": "Sopro Flamejante", "agua": "Jato d'Água", "raio": "Faísca Cortante", "terra": "Estaca Rústica", "vento": "Lâmina de Vento"}}


def _montar() -> dict:
    j = {}
    for e, nomes in NINJUTSU.items():
        for t, n in enumerate(nomes, 1):
            j[f"ninjutsu_{e}_{t}"] = {
                "id": f"ninjutsu_{e}_{t}", "nome": n, "cat": "ninjutsu", "elem": e, "tier": t, "poder": PODER_NIN[t - 1], "custo": CUSTO[t - 1],
                "desc": f"Ninjutsu de {ELEMENTOS[e][0]}: {int(PODER_NIN[t - 1] * 100)}% do dano de um ninjutsu comum. "
                        f"Forte contra {ELEMENTOS[VANTAGEM[e]][0]}, fraco contra {ELEMENTOS[FRACO_CONTRA[e]][0]}."}
    for t in range(1, 6):
        j[f"taijutsu_x_{t}"] = {"id": f"taijutsu_x_{t}", "nome": TAIJUTSU[t - 1], "cat": "taijutsu", "elem": None, "tier": t,
                                "poder": PODER_TAI[t - 1], "custo": round(CUSTO[t - 1] * 0.5, 2),
                                "desc": f"Golpe físico: {int(PODER_TAI[t - 1] * 100)}% do dano de um golpe comum, +15% de chance de crítico e custo baixo de chakra."}
        j[f"genjutsu_x_{t}"] = {"id": f"genjutsu_x_{t}", "nome": GENJUTSU[t - 1], "cat": "genjutsu", "elem": None, "tier": t,
                                "poder": PODER_GEN[t - 1], "custo": CUSTO[t - 1],
                                "desc": f"Ilusão: {int(PODER_GEN[t - 1] * 100)}% do dano de um genjutsu comum e 45% de chance de atordoar o inimigo."}
    return j


JUTSUS = _montar()


# ───────── Teste do elemento (criação do personagem) ─────────
# Cada pergunta tem 5 respostas, uma por elemento, em ordens diferentes. O servidor soma os votos.
QUIZ = [
    {"p": "Diante de um inimigo mais forte que você, o que faz?",
     "o": [("Espera o ritmo dele e se adapta", "agua"), ("Firma os pés e resiste ao impacto", "terra"), ("Ataca de frente, com toda a fúria", "fogo"),
           ("Desaparece no ar e muda o ângulo", "vento"), ("Golpeia no instante exato, o mais rápido possível", "raio")]},
    {"p": "Qual destes lugares mais combina com você?",
     "o": [("Um planalto onde o vento nunca para", "vento"), ("A beira de um vulcão aceso", "fogo"), ("Uma caverna funda e silenciosa", "terra"),
           ("Uma cascata sob a lua", "agua"), ("O topo de um monte durante a tempestade", "raio")]},
    {"p": "O que você mais valoriza em um companheiro?",
     "o": [("Lealdade que não se quebra", "terra"), ("Velocidade para agir primeiro", "raio"), ("Liberdade para seguir o próprio caminho", "vento"),
           ("Coragem para enfrentar qualquer coisa", "fogo"), ("Calma para mudar de plano sem perder a cabeça", "agua")]},
    {"p": "Qual é o seu maior defeito, com sinceridade?",
     "o": [("Impaciência", "fogo"), ("Distração", "vento"), ("Teimosia", "terra"), ("Indecisão", "agua"), ("Impulsividade", "raio")]},
    {"p": "Qual som mais acalma você?",
     "o": [("A chuva batendo no telhado", "agua"), ("Um trovão distante", "raio"), ("O estalar da lenha na fogueira", "fogo"),
           ("O silêncio pesado das pedras", "terra"), ("O assobio do vento entre as árvores", "vento")]},
]


def elemento_do_teste(respostas: list) -> str:
    """Elemento base pelo teste: o mais votado. Empates são sorteados."""
    import random
    votos = {}
    for q, r in zip(QUIZ, respostas):
        e = q["o"][r][1]
        votos[e] = votos.get(e, 0) + 1
    topo = max(votos.values())
    return random.choice([e for e, v in votos.items() if v == topo])
