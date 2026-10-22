ENERGIA_MAX = 25
MIN_POR_ENERGIA = 5      # 1 ponto de energia a cada 5 minutos
CUSTO_EXPLORAR = 5
CUSTO_ARENA = 4
CUSTO_MASMORRA = 6
MASMORRA_ONDAS = 10
# dificuldade: (força dos monstros, multiplicador de recompensa)
DIF = {1: (1.0, 1.0), 2: (1.6, 1.8), 3: (2.4, 3.0)}
TAXA_ETER = 200          # moedas por 1 Éter

AREAS = [
    {"id": "centro", "nome": "Centro", "icone": "🏙️"},
    {"id": "arredores", "nome": "Arredores", "icone": "🌲"},
]

# x/y em % do mapa. "pronto": False = local encoberto pela névoa (ainda não construído).
def _l(id, nome, icone, area, x, y, desc, pronto=True):
    return {"id": id, "nome": nome, "icone": icone, "area": area, "x": x, "y": y, "descricao": desc, "pronto": pronto}

LOCAIS = [
    _l("academia", "Grande Academia", "🏛️", "centro", 50, 16, "Estude as sagas e descubra os segredos de Kazan."),
    _l("taverna", "Taverna da Lua", "🍺", "centro", 22, 38, "Descanse, coma e ouça rumores da cidade."),
    _l("casa", "Seu Quarto", "🏠", "centro", 78, 38, "Seu perfil e código de convite."),
    _l("missoes", "Quadro de Missões", "📜", "centro", 25, 62, "Tarefas diárias com recompensa."),
    _l("conquistas", "Salão de Conquistas", "🏅", "centro", 75, 62, "Suas conquistas e recompensas."),
    _l("banco", "Banco do Éter", "🏦", "centro", 50, 84, "Troque moedas por Éter, a moeda rara."),
    _l("portal", "Portal das Brumas", "🌀", "arredores", 50, 16, "Explore terras desconhecidas gastando energia."),
    _l("praca", "Praça das Aldeias", "🏘️", "arredores", 22, 38, "Ranking dos maiores mestres de Kazan."),
    _l("mercado", "Mercado das Sombras", "🏪", "arredores", 78, 38, "Compre e venda itens raros."),
    _l("arena", "Arena dos Mestres", "⚔️", "arredores", 25, 62, "Desafie outros mestres em batalha."),
    _l("ferraria", "Ferraria", "🔨", "arredores", 75, 62, "Melhore seus equipamentos."),
    _l("convites", "Salão dos Convites", "🤝", "arredores", 50, 84, "Convide amigos e ganhe recompensas.", False),
]

REFEICOES = [
    {"id": "sopa", "nome": "Sopa quente", "custo": 10, "energia": 5},
    {"id": "banquete", "nome": "Banquete da Lua", "custo": 30, "energia": 20},
]

# "contador" liga a missão a uma ação do jogo (ver banco.contar)
# exp = % da barra do nível atual (escala sozinho até o nível 100); moedas escalam com o nível
MISSOES = [
    {"id": "m_explorar", "nome": "Desbravador", "descricao": "Explore o Portal das Brumas", "contador": "explorar", "meta": 3, "exp": 30, "moedas": 20},
    {"id": "m_comer", "nome": "Pausa na Taverna", "descricao": "Faça uma refeição na Taverna", "contador": "comer", "meta": 1, "exp": 8, "moedas": 10},
    {"id": "m_capitulo", "nome": "Estudante Dedicado", "descricao": "Conclua um capítulo novo na Academia", "contador": "capitulo", "meta": 1, "exp": 30, "moedas": 10},
    {"id": "m_arena", "nome": "Duelista", "descricao": "Vença uma batalha na Arena", "contador": "arena_vitoria", "meta": 1, "exp": 25, "moedas": 20},
]

EXPLORACOES = [
    "Você atravessa a névoa e encontra ruínas esquecidas.",
    "Sussurros guiam você até uma clareira iluminada pela lua.",
    "Um mercador fantasma deixa cair uma bolsa ao passar.",
    "Você segue pegadas brilhantes até uma caverna cheia de runas.",
]

RUMORES = [
    "Dizem que a biblioteca da Academia tem uma porta que só aparece à meia-noite.",
    "Um relojoeiro estranho tem comprado todos os relógios quebrados da cidade.",
    "Os desaparecidos de Kazan sempre somem em noites de lua nova.",
    "Mestres de todas as aldeias viajam para cá. Ninguém sabe por quê.",
]

# ---------------- Itens (catálogo gerado em itens.py) ----------------
from itens import ITENS, MAX_MELHORIA, RARIDADES, custo_melhoria  # noqa: E402,F401
