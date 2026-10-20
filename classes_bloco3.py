"""
classes_bloco3.py — Regras puras do Bloco 3: Marionetista e Ninja Médico.

Sem banco, sem FastAPI e sem imports do jogo: só funções que recebem e devolvem
dicionários simples (JSON). Isso permite testar tudo isolado (test_classes_bloco3.py)
e guardar o estado em colunas JSON do Player / no dict da luta (p.luta).

Todo número vem das constantes deste arquivo: o cliente nunca informa custo, cura ou dano.
Erros de regra levantam RegraErro(msg); a camada HTTP converte em 400/409.
"""
from __future__ import annotations

import math
import random
from typing import Iterable, Optional


class RegraErro(ValueError):
    """Violação de regra (chakra insuficiente, limite de cura, etc.)."""


# ═════════════════════ MARIONETISTA ═════════════════════
MAR_INVOCAR_CUSTO = 20          # chakra para invocar
MAR_MANUTENCAO_CUSTO = 2        # chakra por turno com a marionete ativa
MAR_SLOTS_MODULO = 3
MAR_INSTALAR_RYOS = 150
MAR_LINHAS_CORTADAS_TURNOS = 2  # turnos sem proteção depois de ter as linhas cortadas
MAR_PROPS_CONTORNO = frozenset({"perfura", "rompe_linhas", "ignora_marionete"})

ESPECS = ("armadilhas", "combate", "elementais")
ESPEC_MAX_POR_RAMO = 5
ESPEC_MAX_TOTAL = 8             # 3 ramos x 5 = 15 > 8: nunca dá para maximizar tudo
ELEMENTOS_MOD = ("fogo", "agua", "vento", "raio", "terra")

# custo = chakra; recarga = turnos; poder = multiplicador do golpe (Bloco 7)
MODULOS = {
    "laminas":   {"nome": "Lâminas Giratórias", "custo": 8,  "recarga": 1, "poder": 1.00, "elem": None,    "efeito": None},
    "armadilha": {"nome": "Armadilha de Fios",  "custo": 12, "recarga": 4, "poder": 0.70, "elem": None,    "efeito": "atordoa"},
    "fumaca":    {"nome": "Bomba de Fumaça",    "custo": 10, "recarga": 5, "poder": 0.00, "elem": None,    "efeito": "cego"},
    "fogo":      {"nome": "Módulo de Fogo",     "custo": 14, "recarga": 3, "poder": 1.20, "elem": "fogo",  "efeito": None},
    "agua":      {"nome": "Módulo de Água",     "custo": 14, "recarga": 3, "poder": 1.15, "elem": "agua",  "efeito": None},
    "vento":     {"nome": "Módulo de Vento",    "custo": 14, "recarga": 3, "poder": 1.15, "elem": "vento", "efeito": None},
    "raio":      {"nome": "Módulo de Relâmpago", "custo": 15, "recarga": 3, "poder": 1.20, "elem": "raio",  "efeito": None},
    "terra":     {"nome": "Módulo de Terra",    "custo": 14, "recarga": 3, "poder": 1.15, "elem": "terra", "efeito": None},
}

AGULHA_CUSTO = 6
AGULHA_PODER = 0.85
AGULHA_CRIT_EXTRA = 0.10
VENENO_CUSTO = 10
VENENO_TURNOS = 3
VENENO_MAX_STACKS = 5
VENENO_TETO_TICK_PCT = 0.06     # um tick nunca passa de 6% da vida máxima do alvo


def estado_marionete_padrao() -> dict:
    return {"hp": 0, "hp_max": 0, "ativa": False, "criada": False, "modulos": [],
            "espec": {e: 0 for e in ESPECS}, "cd": {}, "suspensa": 0}


def _u(stats_universais: dict, k: str) -> int:
    return max(0, int(stats_universais.get(k) or 0))


def hp_max_marionete(u: dict, espec: Optional[dict] = None) -> int:
    espec = espec or {}
    return int(40 + 4 * _u(u, "int") + 5 * _u(u, "vig") + 6 * espec.get("combate", 0))


def defesa_marionete(u: dict, espec: Optional[dict] = None) -> int:
    espec = espec or {}
    return min(200, int(0.5 * _u(u, "vig") + 2 * espec.get("combate", 0)))


def normalizar(estado: Optional[dict], u: dict) -> dict:
    """Completa campos ausentes (jogadores antigos) e recalcula o teto de HP. Devolve uma cópia."""
    e = estado_marionete_padrao()
    e.update({k: v for k, v in (estado or {}).items() if k in e})
    e["espec"] = {k: max(0, min(ESPEC_MAX_POR_RAMO, int((estado or {}).get("espec", {}).get(k, 0)))) for k in ESPECS}
    e["modulos"] = [m for m in dict.fromkeys(e["modulos"]) if m in MODULOS][:MAR_SLOTS_MODULO]
    e["cd"] = {k: max(0, int(v)) for k, v in (e["cd"] or {}).items() if k in MODULOS}
    novo_max = hp_max_marionete(u, e["espec"])
    if not e["criada"]:
        e["criada"], e["hp"] = True, novo_max
    e["hp_max"] = novo_max
    e["hp"] = max(0, min(novo_max, int(e["hp"])))
    e["suspensa"] = max(0, int(e["suspensa"]))
    if e["hp"] <= 0:
        e["ativa"] = False
    return e


def invocar(estado: dict, chakra_atual: int) -> tuple[dict, int]:
    e = dict(estado)
    if e["ativa"]:
        raise RegraErro("A marionete já está ativa.")
    if e["hp"] <= 0:
        raise RegraErro("A marionete está destruída: descanse ou vá ao hospital para consertá-la.")
    if chakra_atual < MAR_INVOCAR_CUSTO:
        raise RegraErro(f"Chakra insuficiente: invocar custa {MAR_INVOCAR_CUSTO}.")
    e["ativa"], e["suspensa"] = True, 0
    return e, MAR_INVOCAR_CUSTO


def manutencao(estado: dict, chakra_atual: int) -> tuple[dict, int, bool]:
    """Cobra a manutenção do turno. Sem chakra, a marionete se desativa (o HP é mantido)."""
    e = dict(estado)
    if not e["ativa"]:
        return e, 0, False
    if chakra_atual < MAR_MANUTENCAO_CUSTO:
        e["ativa"] = False
        return e, 0, True
    return e, MAR_MANUTENCAO_CUSTO, False


def protegendo(estado: dict) -> bool:
    return bool(estado["ativa"] and estado["hp"] > 0 and estado["suspensa"] <= 0)


def absorver(estado: dict, dano: int, defesa: int = 0, props: Iterable[str] = ()) -> tuple[dict, int, int, str]:
    """
    Aplica um ataque comum contra o jogador. Devolve (novo_estado, dano_ao_jogador, dano_na_marionete, evento).
    - Protegida: a marionete absorve 100% (já reduzido pela defesa dela). O que passar do HP restante vaza ao jogador.
    - Propriedades perfura / rompe_linhas / ignora_marionete contornam a proteção.
    - rompe_linhas também suspende a marionete por MAR_LINHAS_CORTADAS_TURNOS turnos.
    """
    e, dano, props = dict(estado), max(0, int(dano)), set(props)
    if dano == 0:
        return e, 0, 0, "nenhum"
    if not (e["ativa"] and e["hp"] > 0):
        return e, dano, 0, "sem_marionete"
    if "rompe_linhas" in props:
        e["suspensa"] = max(e["suspensa"], MAR_LINHAS_CORTADAS_TURNOS)
        return e, dano, 0, "linhas_cortadas"
    if e["suspensa"] > 0 or props & MAR_PROPS_CONTORNO:
        return e, dano, 0, "contornou"
    na_mar = max(1, int(dano * 100 / (100 + max(0, defesa))))
    if na_mar <= e["hp"]:
        e["hp"] -= na_mar
        evento = "protegido"
        vaza = 0
    else:
        vaza, na_mar, e["hp"] = na_mar - e["hp"], e["hp"], 0
        evento = "quebrou"
    if e["hp"] <= 0:
        e["ativa"] = False
        evento = "quebrou"
    return e, vaza, na_mar, evento


def passar_turno(estado: dict) -> dict:
    """No fim de cada turno completo: reduz recargas e a suspensão das linhas."""
    e = dict(estado)
    e["cd"] = {k: v - 1 for k, v in e["cd"].items() if v - 1 > 0}
    e["suspensa"] = max(0, e["suspensa"] - 1)
    return e


def cortar_linhas(estado: dict) -> dict:
    """Contrajogada: linhas de chakra cortadas (PvP/PvE)."""
    e = dict(estado)
    e["suspensa"] = max(e["suspensa"], MAR_LINHAS_CORTADAS_TURNOS)
    return e


def recuperar(estado: dict, fracao: float) -> dict:
    """Recuperação fora de combate (descanso = 1.0, hospital gratuito = 0.5 do HP perdido)."""
    e = dict(estado)
    fracao = max(0.0, min(1.0, fracao))
    falta = e["hp_max"] - e["hp"]
    e["hp"] = min(e["hp_max"], e["hp"] + math.ceil(falta * fracao)) if falta > 0 else e["hp"]
    e["suspensa"], e["cd"] = 0, {}
    return e


def custo_modulo(mid: str, espec: dict) -> int:
    m = MODULOS[mid]
    desc = 0.03 * espec.get("elementais", 0) if m["elem"] else 0.0
    return max(1, round(m["custo"] * (1 - desc)))


def poder_modulo(mid: str, espec: dict) -> float:
    m = MODULOS[mid]
    p = m["poder"]
    if m["elem"]:
        p *= 1 + 0.04 * espec.get("elementais", 0)
    elif mid == "armadilha":
        p *= 1 + 0.08 * espec.get("armadilhas", 0)
    return round(p, 3)


def recarga_modulo(mid: str, espec: dict) -> int:
    base = MODULOS[mid]["recarga"]
    return max(1, base - (1 if mid in ("armadilha", "fumaca") and espec.get("armadilhas", 0) >= 3 else 0))


def usar_modulo(estado: dict, mid: str, chakra_disp: int) -> tuple[dict, int]:
    """Valida e registra o uso: devolve (estado com recarga, custo de chakra)."""
    if mid not in MODULOS:
        raise RegraErro("Módulo desconhecido.")
    if not protegendo(estado) and not estado["ativa"]:
        raise RegraErro("Invoque a marionete antes de usar módulos.")
    if mid not in estado["modulos"]:
        raise RegraErro("Esse módulo não está instalado na sua marionete.")
    if estado["cd"].get(mid, 0) > 0:
        raise RegraErro(f"{MODULOS[mid]['nome']} em recarga: {estado['cd'][mid]} turno(s).")
    custo = custo_modulo(mid, estado["espec"])
    if chakra_disp < custo:
        raise RegraErro(f"Chakra insuficiente: {MODULOS[mid]['nome']} custa {custo}.")
    e = dict(estado)
    e["cd"] = {**e["cd"], mid: recarga_modulo(mid, e["espec"]) + 1}  # +1: o fim do turno atual já desconta 1
    return e, custo


def instalar_modulo(estado: dict, mid: str, ryos: int) -> tuple[dict, int]:
    if mid not in MODULOS:
        raise RegraErro("Módulo desconhecido.")
    if mid in estado["modulos"]:
        raise RegraErro("Módulo já instalado.")
    if len(estado["modulos"]) >= MAR_SLOTS_MODULO:
        raise RegraErro("A marionete não tem mais slots de módulo.")
    if ryos < MAR_INSTALAR_RYOS:
        raise RegraErro(f"Ryos insuficientes: instalar custa {MAR_INSTALAR_RYOS}.")
    e = dict(estado)
    e["modulos"] = [*e["modulos"], mid]
    return e, MAR_INSTALAR_RYOS


def remover_modulo(estado: dict, mid: str) -> dict:
    if mid not in estado["modulos"]:
        raise RegraErro("Esse módulo não está instalado.")
    e = dict(estado)
    e["modulos"] = [m for m in e["modulos"] if m != mid]
    e["cd"] = {k: v for k, v in e["cd"].items() if k != mid}
    return e


def pontos_espec_livres(level: int, espec: dict) -> int:
    return max(0, min(ESPEC_MAX_TOTAL, int(level) // 4) - sum(espec.values()))


def investir_espec(estado: dict, ramo: str, level: int) -> dict:
    if ramo not in ESPECS:
        raise RegraErro("Especialização desconhecida.")
    if estado["espec"][ramo] >= ESPEC_MAX_POR_RAMO:
        raise RegraErro("Esse ramo já está no máximo.")
    if sum(estado["espec"].values()) >= ESPEC_MAX_TOTAL:
        raise RegraErro(f"Limite de {ESPEC_MAX_TOTAL} pontos de especialização atingido: escolha seus caminhos.")
    if pontos_espec_livres(level, estado["espec"]) <= 0:
        raise RegraErro("Sem pontos de especialização disponíveis (1 a cada 4 níveis).")
    e = dict(estado)
    e["espec"] = {**e["espec"], ramo: e["espec"][ramo] + 1}
    return e


# ── Veneno ──
def dano_veneno_base(u: dict) -> int:
    return max(2, int(0.5 * _u(u, "int") + 0.3 * _u(u, "dex")))


def aplicar_veneno(veneno: Optional[dict], dano: int, resistencia: float, rng=random) -> tuple[Optional[dict], bool]:
    """Empilha veneno (máx. VENENO_MAX_STACKS) e renova a duração. `resistencia` em 0..0.6 = chance de resistir."""
    if rng.random() < max(0.0, min(0.6, resistencia)):
        return veneno, False
    v = dict(veneno or {"stacks": 0, "dano": 0, "turnos": 0})
    v["stacks"] = min(VENENO_MAX_STACKS, v["stacks"] + 1)
    v["dano"] = max(v["dano"], max(1, int(dano)))
    v["turnos"] = VENENO_TURNOS
    return v, True


def tick_veneno(veneno: Optional[dict], hp_max_alvo: int) -> tuple[Optional[dict], int]:
    if not veneno or veneno["turnos"] <= 0:
        return None, 0
    dano = min(veneno["dano"] * veneno["stacks"], max(1, int(hp_max_alvo * VENENO_TETO_TICK_PCT)))
    v = {**veneno, "turnos": veneno["turnos"] - 1}
    return (v if v["turnos"] > 0 else None), dano


def resistencia_veneno(vig_alvo: int) -> float:
    return min(0.6, max(0, vig_alvo) / (max(0, vig_alvo) + 150))


def tratar_veneno(veneno: Optional[dict]) -> None:
    """Antídoto ou habilidade de tratamento remove tudo."""
    return None


# ── Agulhas ──
def efeito_agulha(espec_combate: int, rng=random) -> Optional[str]:
    if rng.random() < 0.20 + 0.03 * espec_combate:
        return rng.choice(("sangrando", "cego"))
    return None


# ── Propriedades dos ataques inimigos (balanceamento PvE/PvP) ──
def propriedade_ataque_inimigo(tipo_luta: str, boss: bool, rng=random) -> Optional[str]:
    """Chefes e rivais de PvP têm ataques que desafiam a marionete (sem imunidade permanente)."""
    if tipo_luta in ("pvp", "arena"):
        pr, pp = 0.20, 0.10
    elif boss:
        pr, pp = 0.18, 0.15
    else:
        return None
    r = rng.random()
    return "rompe_linhas" if r < pr else "perfura" if r < pr + pp else None


# ═════════════════════ NINJA MÉDICO ═════════════════════
CURAS = {
    "palma": {"nome": "Palma Curativa",       "custo": 30, "pct": 0.18, "recarga": 0},
    "selo":  {"nome": "Selo Regenerador",     "custo": 60, "pct": 0.35, "recarga": 4},
}
MAX_CURAS_TURNO = 1             # ação livre, mas só uma por turno
MAX_CURAS_LUTA = 6              # teto por luta
CURA_ESCALA_CUSTO = 10          # cada cura na luta encarece as próximas em +10 de chakra
BISTURI_CUSTO_MULT = 1.5        # x custo_tecnica(me)
BISTURI_PODER = 1.15
BISTURI_TETO_PCT = 0.25         # dano verdadeiro por golpe nunca passa de 25% da vida máxima do alvo


def estado_cura_padrao() -> dict:
    return {"turno": -1, "usos_turno": 0, "total": 0, "cd": {}}


def custo_cura(tecnica: str, estado_cura: dict) -> int:
    return CURAS[tecnica]["custo"] + CURA_ESCALA_CUSTO * estado_cura["total"]


def curar(estado_cura: dict, tecnica: str, turno: int, chakra: int, vida: int, vida_max: int, u_int: int) -> tuple[dict, int, int]:
    """Cura como ação livre. Devolve (novo_estado_cura, vida_curada, chakra_gasto) ou levanta RegraErro."""
    if tecnica not in CURAS:
        raise RegraErro("Técnica médica desconhecida.")
    ec = {**estado_cura, "cd": dict(estado_cura["cd"])}
    if vida >= vida_max:
        raise RegraErro("Você já está com a vida cheia.")
    if ec["turno"] == turno and ec["usos_turno"] >= MAX_CURAS_TURNO:
        raise RegraErro("Você já curou neste turno: ataque para avançar.")
    if ec["total"] >= MAX_CURAS_LUTA:
        raise RegraErro(f"Limite de {MAX_CURAS_LUTA} curas por luta atingido.")
    if ec["cd"].get(tecnica, -1) > turno:
        raise RegraErro(f"{CURAS[tecnica]['nome']} em recarga por mais {ec['cd'][tecnica] - turno} turno(s).")
    custo = custo_cura(tecnica, ec)
    if chakra < custo:
        raise RegraErro(f"Chakra insuficiente: {CURAS[tecnica]['nome']} custa {custo}.")
    bruta = int(vida_max * CURAS[tecnica]["pct"] + 1.5 * max(0, u_int))
    cura = max(1, min(vida_max - vida, bruta))
    ec["usos_turno"] = ec["usos_turno"] + 1 if ec["turno"] == turno else 1
    ec["turno"], ec["total"] = turno, ec["total"] + 1
    ec["cd"][tecnica] = turno + CURAS[tecnica]["recarga"]
    return ec, cura, custo


def custo_bisturi(custo_tecnica_base: int) -> int:
    return max(1, round(custo_tecnica_base * BISTURI_CUSTO_MULT))


def limitar_verdadeiro(dano: int, hp_max_alvo: int) -> int:
    """Teto do dano verdadeiro: preserva o limite da habilidade sem aplicar o Vigor outra vez."""
    return 0 if dano <= 0 else min(int(dano), max(1, int(hp_max_alvo * BISTURI_TETO_PCT)))


# ── Passiva fora de combate ──
PASSIVAS_MEDICAS = ("hospital", "tratamento")
HOSPITAL_CD_BASE = 600
HOSPITAL_CD_MEDICO = 300        # -50%
TRATAMENTO_DESCONTO = 0.30


def escolher_passiva(classe: str, atual: Optional[str], opcao: str) -> str:
    if classe != "ninja_medico":
        raise RegraErro("Só o Ninja Médico tem passivas médicas.")
    if opcao not in PASSIVAS_MEDICAS:
        raise RegraErro("Passiva inválida.")
    if atual:
        raise RegraErro("Você já escolheu sua passiva médica.")
    return opcao


def cd_hospital(classe: str, passiva: Optional[str], base: int = HOSPITAL_CD_BASE) -> int:
    return base // 2 if classe == "ninja_medico" and passiva == "hospital" else base


def preco_tratamento(preco: int, classe: str, passiva: Optional[str]) -> int:
    if classe == "ninja_medico" and passiva == "tratamento":
        return max(1, int(round(preco * (1 - TRATAMENTO_DESCONTO))))
    return preco
