"""
bloco4.py — Bloco 4 do win.txt: curva de spawn, Pity do Boss e tabela de drops da exploração.

Só regras e sorteios (sem banco, sem FastAPI), para poder testar com semente fixa. A ligação com o banco e com o
inventário fica em game_extra.explore().

SPAWN (por clique em Explorar, na faixa de nível do mapa)
    Chefe do mapa .... 1% base (nível fixo = topo da faixa), +1% a cada 10 Pontos de Ameaça, 100% ao chegar em 100
    Fácil ............ 55%  (primeiros ~30% dos níveis da faixa; no mapa 1-10: níveis 1-3)
    Médio ............ 33%  (~40% do meio; níveis 4-7)
    Difícil .......... 11%  (últimos ~30%; níveis 8-10)
    Os pesos 55:33:11 são mantidos mesmo quando o pity aumenta a chance do chefe (o resto é redistribuído na mesma
    proporção), então o total sempre fecha em 100%.

PITY
    Clique sem Boss: +1 Ponto de Ameaça (máx. 100). Clique com Boss: volta a 0 no ato.

DROPS (dois rolos independentes por clique)
    Rolo A (materiais): 60% 1-3 básicos do tier | 10% 1 raro | 30% vazio
    Rolo B (equip.):    75% vazio | 15% fragmento de mapa | 8% comum | 1,8% incomum | 0,2% raro
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional

PITY_MAX = 100
BOSS_BASE_PCT = 1.0
PESOS_FAIXA = (("facil", 55), ("medio", 33), ("dificil", 11))


def chance_chefe(pontos: int) -> float:
    """Chance de spawn do chefe em %: 1% + 1% por 10 pontos; 100% com 100 pontos."""
    if pontos >= PITY_MAX:
        return 100.0
    return BOSS_BASE_PCT + (max(0, pontos) // 10)


def faixas_nivel(lo: int, hi: int) -> dict:
    """Divide a faixa de nível do mapa em fácil / médio / difícil (30% / 40% / 30%).
    No mapa 1-10 dá exatamente 1-3, 4-7 e 8-10, como no roteiro."""
    n = hi - lo + 1
    f = max(1, round(n * 0.3))
    d = max(1, round(n * 0.3))
    if f + d >= n:  # faixa minúscula: sobra ao menos 1 nível para o médio
        f, d = max(1, (n - 1) // 2), max(1, (n - 1) // 2)
    m = n - f - d
    return {
        "facil": (lo, lo + f - 1),
        "medio": (lo + f, lo + f + m - 1),
        "dificil": (hi - d + 1, hi),
    }


@dataclass
class Spawn:
    chefe: bool
    faixa: Optional[str]      # "facil" | "medio" | "dificil" | None (chefe)
    nivel: int
    pontos: int               # Pontos de Ameaça DEPOIS deste clique
    chance_chefe: float       # chance (em %) que valia NESTE clique


def rolar_spawn(pontos: int, lo: int, hi: int, forcar_chefe: bool = False,
                rng: Optional[random.Random] = None) -> Spawn:
    rng = rng or random
    chance = chance_chefe(pontos)
    if forcar_chefe or rng.random() * 100 < chance:
        return Spawn(True, None, hi, 0, chance)  # chefe: nível fixo no topo da faixa; pity zera

    faixas = faixas_nivel(lo, hi)
    total = sum(p for _, p in PESOS_FAIXA)
    r, acc = rng.random() * total, 0
    for nome, peso in PESOS_FAIXA:
        acc += peso
        if r < acc:
            a, b = faixas[nome]
            return Spawn(False, nome, rng.randint(a, b), min(PITY_MAX, pontos + 1), chance)
    raise RuntimeError("inalcançável")


def rolar_materiais(rng: Optional[random.Random] = None):
    """Rolo A. Retorna None, ('basico', qtd) ou ('raro', 1)."""
    rng = rng or random
    r = rng.random() * 100
    if r < 60:
        return ("basico", rng.randint(1, 3))
    if r < 70:
        return ("raro", 1)
    return None


def rolar_equipamento(rng: Optional[random.Random] = None) -> Optional[str]:
    """Rolo B. Retorna None, 'fragmento', 'comum', 'incomum' ou 'raro'."""
    rng = rng or random
    r = rng.random() * 100
    if r < 75:
        return None
    if r < 90:
        return "fragmento"
    if r < 98:
        return "comum"
    if r < 99.8:
        return "incomum"
    return "raro"


if __name__ == "__main__":
    rng = random.Random(1)
    print("faixas 1-10:", faixas_nivel(1, 10))
    n = 200_000
    cont = {"chefe": 0, "facil": 0, "medio": 0, "dificil": 0}
    for _ in range(n):
        s = rolar_spawn(0, 1, 10, rng=rng)
        cont["chefe" if s.chefe else s.faixa] += 1
    print({k: round(v / n * 100, 2) for k, v in cont.items()})
