from dataclasses import dataclass
from typing import Optional, List
from datetime import datetime

@dataclass
class Capitulo:
    id: str
    titulo: str
    texto: str
    requisito: Optional[dict] = None
    recompensas: Optional[dict] = None
    proximo: Optional[str] = None

@dataclass
class Saga:
    id: str
    titulo: str
    descricao: str
    capitulos: List[Capitulo]

CAPS_CAMINHO = [
    Capitulo(
        id="cap1", titulo="🌙 O Despertar",
        texto="Você acorda em frente às portas da Grande Academia de Kazan... O Éter esperava por você.",
        recompensas={"exp": 50, "moedas": 10}, proximo="cap2"
    ),
    Capitulo(
        id="cap2", titulo="⭐ Sua Tradição",
        texto="Cinco mestres o aguardam em um salão circular. Existe apenas o SEU caminho.",
        requisito={"nivel": 1}, recompensas={"exp": 80}, proximo="cap3"
    ),
    Capitulo(
        id="cap3", titulo="📚 A Biblioteca Proibida",
        texto="No último andar da Academia, atrás de uma estante que range sozinha, você encontra o diário de um mestre desaparecido. A última página diz: O Éter não é apenas poder. É memória. E alguém está colhendo as nossas.",
        requisito={"nivel": 3}, recompensas={"exp": 150, "moedas": 40}, proximo="cap4"
    ),
    Capitulo(
        id="cap4", titulo="🕰️ O Relojoeiro de Kazan",
        texto="Numa viela escura, um relojoeiro vende relógios que não marcam as horas. Cada um está parado no instante exato em que alguém sumiu da cidade. Ele sorri ao ver o seu: Ainda não, pequeno mestre. Ainda não.",
        requisito={"nivel": 5}, recompensas={"exp": 240, "moedas": 60}, proximo="cap5"
    ),
    Capitulo(
        id="cap5", titulo="🌫️ Os Desaparecidos",
        texto="Na Praça das Aldeias, os nomes dos desaparecidos cobrem as paredes. Todos eram mestres de uma das cinco tradições. Todos sumiram em noites de lua nova. A próxima lua nova é daqui a poucos dias.",
        requisito={"nivel": 7}, recompensas={"exp": 320, "moedas": 80}, proximo="cap6"
    ),
    Capitulo(
        id="cap6", titulo="🗝️ A Porta de Névoa",
        texto="O Portal das Brumas guarda uma porta que ninguém abriu. Quando você se aproxima, a névoa sussurra o seu nome com a voz de alguém que você já perdeu. A porta se abre devagar.",
        requisito={"nivel": 10}, recompensas={"exp": 440, "moedas": 110}, proximo="cap7"
    ),
    Capitulo(
        id="cap7", titulo="⚖️ O Conselho dos Cinco",
        texto="Os mestres das cinco tradições se reúnem em segredo. Quatro querem fechar a Academia até a ameaça passar. O quinto insiste em manter as portas abertas. Você percebe que ele é o único que não demonstra medo.",
        requisito={"nivel": 13}, recompensas={"exp": 540, "moedas": 150}, proximo="cap8"
    ),
    Capitulo(
        id="cap8", titulo="🌑 A Sombra do Mestre",
        texto="Nas profundezas da torre, você encontra a sua própria sombra, mas ela se move sozinha. O Éter devolve o que toma, diz ela. A pergunta é: o que você aceitaria perder para trazer os desaparecidos de volta?",
        requisito={"nivel": 16}, recompensas={"exp": 620, "moedas": 200}, proximo="cap9"
    ),
    Capitulo(
        id="cap9", titulo="⏳ O Coração do Relógio",
        texto="No centro da cidade existe um relógio gigante que ninguém jamais viu. Cada engrenagem guarda uma memória roubada. No topo, o relojoeiro espera por você com o relógio parado que carrega o seu nome.",
        requisito={"nivel": 20}, recompensas={"exp": 760, "moedas": 260}, proximo="cap10"
    ),
    Capitulo(
        id="cap10", titulo="🌅 O Nascer de Kazan",
        texto="Você quebra o último ponteiro. As memórias voam como vaga-lumes e os desaparecidos retornam, um por um, confusos e vivos. O relojoeiro sorri pela última vez e some. O céu amanhece. Mas no horizonte, uma nova torre acaba de surgir... (Fim do Ato I)",
        requisito={"nivel": 25}, recompensas={"exp": 940, "moedas": 400}, proximo=None
    )
]

TODAS_AS_SAGAS = [
    Saga(id="caminho_do_eter", titulo="🌙 O Caminho do Éter",
          descricao="Sua jornada começa...", capitulos=CAPS_CAMINHO)
]

def listar_sagas():
    return [{"id": s.id, "titulo": s.titulo, "descricao": s.descricao} for s in TODAS_AS_SAGAS]

def obter_inicio_saga(saga_id):
    for s in TODAS_AS_SAGAS:
        if s.id == saga_id and s.capitulos:
            return s.capitulos[0]
    return None

def buscar_capitulo(saga_id, cap_id):
    for s in TODAS_AS_SAGAS:
        if s.id == saga_id:
            for c in s.capitulos:
                if c.id == cap_id:
                    return c
    return None

def verificar_requisito(cap, jogador):
    if not cap.requisito:
        return True
    for k, v in cap.requisito.items():
        if jogador.get(k, 0) < v:
            return False
    return True

def salvar_progresso(db, jogador_id, saga_id, ultimo_cap):
    db.execute("""
        INSERT OR REPLACE INTO progresso_historia(player_id, saga_id, ultimo_capitulo, desbloqueado_em)
        VALUES (?, ?, ?, ?)
    """, (jogador_id, saga_id, ultimo_cap, datetime.now()))

def obter_progresso(db, jogador_id, saga_id):
    res = db.execute("""
        SELECT ultimo_capitulo, concluido FROM progresso_historia
        WHERE player_id = ? AND saga_id = ?
    """, (jogador_id, saga_id)).fetchone()
    if res:
        return {"capitulo_atual": res[0], "concluido": bool(res[1])}
    return {"capitulo_atual": None, "concluido": False}
