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
        requisito={"nivel": 1}, recompensas={"exp": 80}, proximo=None
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
