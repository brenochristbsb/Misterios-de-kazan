"""
atributos5.py — Ponte entre os 10 atributos antigos e os 5 Atributos Universais (Bloco 2)
+ golpe() do Bloco 7 + mecânicas de classe do Bloco 3.

CONVERSÃO (soma: nenhum ponto some, só muda de gaveta)
STR = taijutsu + forca            DEX = destreza
INT = ninjutsu + genjutsu + inteligencia + chakra_attr
AGI = agilidade                   VIG = vigor + defesa

GOLPE (Bloco 7)
1. Ataque Bruto = principal_da_classe*1.5 + secundario + dano_arma
2. Mitigação    = 100 / (100 + VIG do alvo)         (Ninja Médico: ignora VIG)
3. Elemental    = vantagem/desvantagem conforme a classe (ver CLASSES)
4. Dano         = Bruto * Mitigação * Elemental * poder_do_jutsu * ESCALA_DANO * aleatório(0.95..1.05)

INTEGRAÇÃO COM game_extra.py
golpe() devolve (dano, crítico, atordoa), exatamente o que game_extra espera:
atordoa=True significa que ESTE golpe atordoou o ALVO (só genjutsu, 30%, e só se
causou dano). O game_extra guarda o atordoamento em L["atordoado"]. Os demais efeitos
de classe (fúria, portões, marionete, confusão, dreno) precisam de um EstadoLuta
persistido entre turnos: use golpe_estado() quando isso for ligado ao L da luta.
"""
from __future__ import annotations

import random
from typing import Optional

from combate import CLASSES
from combate_estado import (
    EstadoLuta,
    acumular_furia,
    absorver_dano_marionete,
    rolar_efeito_genjutsu,
    iniciar_turno,
)

POOL = {
    "str": ("taijutsu", "forca"),
    "int": ("ninjutsu", "genjutsu", "inteligencia", "chakra_attr"),
    "agi": ("agilidade",),
    "dex": ("destreza",),
    "vig": ("vigor", "defesa"),
}

ESCALA_DANO = 0.68

# Bloco 3: dano verdadeiro é só do Bisturi de Chakra (golpe com dano_verdadeiro=True).
# True devolve o comportamento antigo (todo ataque do Ninja Médico ignorando o Vigor).
MEDICO_BASICO_VERDADEIRO = False

MULT_CLASSE = {
    "taijutsu": 1.0,
    "ninjutsu": 1.0,
    "genjutsu": 0.75,
    "marionetista": 1.0,
    "ninja_medico": 1.0,
}

GENERICO = {
    "fisico": "str",
    "ninjutsu": "int",
    "genjutsu": "int",
    "secreta": "int",
}


def universais(stats: dict) -> dict:
    """Converte um dicionário com os 10 atributos antigos nos 5 universais."""
    return {
        chave: sum((stats.get(atributo) or 0) for atributo in atributos)
        for chave, atributos in POOL.items()
    }


def classe_efetiva(p) -> str:
    """Retorna a classe escolhida ou infere o estilo antigo com maior atributo."""
    classe = getattr(p, "classe", None)
    if classe in CLASSES:
        return classe

    estilos = {
        "taijutsu": getattr(p, "taijutsu", 0) or 0,
        "ninjutsu": getattr(p, "ninjutsu", 0) or 0,
        "genjutsu": getattr(p, "genjutsu", 0) or 0,
    }
    return max(estilos, key=estilos.get)


def converter_jogador(p) -> None:
    """Grava os 5 atributos universais em Player.atr_*."""
    atributos_antigos = {
        atributo: (getattr(p, atributo, 0) or 0)
        for atributos in POOL.values()
        for atributo in atributos
    }
    u = universais(atributos_antigos)
    p.atr_str, p.atr_int, p.atr_agi, p.atr_dex, p.atr_vig = (
        u["str"], u["int"], u["agi"], u["dex"], u["vig"],
    )


def _princ_sec(att: dict, acao: str):
    classe = att.get("_classe")
    if classe in CLASSES:
        regra = CLASSES[classe]
        return regra["principal"], regra["secundario"], classe
    return GENERICO.get(acao, "int"), "dex", None


def golpe_estado(
    att: dict,
    dfn: dict,
    acao: str,
    poder: float = 1.0,
    mod: float = 1.0,
    crit_extra: float = 0.0,
    rng: Optional[random.Random] = None,
    estado_luta: Optional[EstadoLuta] = None,
    dano_verdadeiro: Optional[bool] = None,
):
    """
    Executa um golpe e retorna (dano, crítico, atordoa, estado_luta).

    atordoa=True: este golpe atordoou o alvo (genjutsu; só se causou dano).
    Dano 0 pode ser esquiva ou absorção total pela marionete do alvo.
    Se o chamador reaproveitar o estado_luta entre turnos, estado.confuso /
    estado.dreno_chakra / estado.efeito_aplicado ficam disponíveis para ele tratar.
    """
    rng = rng or random
    princ, sec, classe = _princ_sec(att, acao)

    if estado_luta is None:
        estado_luta = EstadoLuta(
            classe_atacante=classe or "",
            classe_defensor=dfn.get("_classe"),
            rng=rng if isinstance(rng, random.Random) else random.Random(),
        )
    # O estado é compartilhado: sincroniza quem ataca/defende neste golpe.
    estado_luta.classe_atacante = classe or ""
    estado_luta.classe_defensor = dfn.get("_classe")
    iniciar_turno(estado_luta)

    portoes = classe == "taijutsu" and estado_luta.portoes_ativos > 0

    # Esquiva: agilidade do alvo contra a do atacante (Portões dobram a do atacante).
    agi_defensor = dfn.get("agilidade") or 0
    agi_atacante = (att.get("agilidade") or 0) * (2 if portoes else 1)
    denominador = agi_defensor + agi_atacante + 10
    if denominador > 0 and rng.random() < 0.5 * agi_defensor / denominador:
        return 0, False, False, estado_luta

    a, d = universais(att), universais(dfn)

    # Taijutsu: Portões Internos dobram STR e AGI brutos.
    if portoes:
        a["str"] *= 2
        a["agi"] *= 2

    # 1. Ataque bruto.
    bruto = a[princ] * 1.5 + a[sec] + (att.get("_dano_arma") or 0)

    # 2. Mitigação.
    regra = CLASSES.get(classe, {})
    if dano_verdadeiro is None:  # sem pedido explícito: regra da classe (Ninja Médico só se MEDICO_BASICO_VERDADEIRO)
        dano_verdadeiro = bool(regra.get("dano_verdadeiro")) and MEDICO_BASICO_VERDADEIRO
    vig = 0 if dano_verdadeiro else d["vig"]  # dano verdadeiro: o Vigor não entra, nem reduz uma segunda vez
    mitig = 100 / (100 + vig) if vig > -100 else 1.0

    # 3. Modificador elemental.
    if regra.get("ignora_elemento"):
        elem = 1.0
    elif mod > 1:
        elem = 1.25 if classe == "marionetista" else regra.get("bonus_elemental", 1.25)  # bônus exclusivo é do Ninjutsu
    elif mod < 1:
        elem = 0.75
    else:
        elem = 1.0

    # 4. Dano do turno.
    dano = (
        bruto * mitig * elem * poder * ESCALA_DANO
        * MULT_CLASSE.get(classe, 1.0)
        * rng.uniform(0.95, 1.05)
    )

    # Crítico: chance baseada em destreza.
    destreza = att.get("destreza") or 0
    crit_chance = max(0.0, min(1.0, destreza / (destreza + 60) + crit_extra))
    crit = rng.random() < crit_chance
    if crit:
        dano *= 1.5

    dano_final = max(1, int(dano))

    # Marionete do alvo absorve; o excedente passa.
    dano_final = absorver_dano_marionete(estado_luta, dano_final)

    # Genjutsu: efeito cai no ALVO, depois do golpe (não zera o próprio dano).
    atordoa = False
    if classe == "genjutsu":
        efeito = rolar_efeito_genjutsu(estado_luta)
        atordoa = efeito == "atordoamento" and dano_final > 0

    acumular_furia(estado_luta, dano_final, 0)

    return dano_final, crit, atordoa, estado_luta


def golpe(att, dfn, acao, poder=1.0, mod=1.0, crit_extra=0.0, rng=None, dano_verdadeiro=None):
    """Interface usada por game_extra.golpe: devolve (dano, crítico, atordoa)."""
    dano, crit, atordoa, _ = golpe_estado(att, dfn, acao, poder, mod, crit_extra, rng, dano_verdadeiro=dano_verdadeiro)
    return dano, crit, atordoa


# ────────── Calibração: compara o dano antigo com o novo ──────────
def _golpe_antigo(att, dfn, acao):
    if acao == "fisico":
        raw = att["taijutsu"] + att["forca"] * 0.8 + att["destreza"] * 0.4
        red = dfn["defesa"] * 0.6
    elif acao == "ninjutsu":
        raw = att["ninjutsu"] * 1.3 + att["inteligencia"] * 0.5
        red = dfn["defesa"] * 0.3
    else:
        raw = att["genjutsu"] + att["inteligencia"] * 0.8
        red = dfn["inteligencia"] * 0.5
    return max(1, (raw - red) * 1.3)


def calibrar() -> None:
    """Imprime a razão dano novo/antigo por classe e nível de atributos.
    Compara dano por golpe: sem esquiva (descartada) e sem crítico."""
    global ESCALA_DANO

    perfis = {
        "taijutsu": ("fisico", {"taijutsu": 1.5, "forca": 1.0}),
        "ninjutsu": ("ninjutsu", {"ninjutsu": 1.5, "inteligencia": 1.0}),
        "genjutsu": ("genjutsu", {"genjutsu": 1.5, "inteligencia": 1.0}),
    }
    todos = (
        "taijutsu", "ninjutsu", "genjutsu", "agilidade", "forca",
        "inteligencia", "chakra_attr", "defesa", "vigor", "destreza",
    )

    escala_anterior = ESCALA_DANO
    try:
        ESCALA_DANO = 1.0
        razoes = []
        for classe, (acao, extra) in perfis.items():
            linha = []
            for b in (5, 10, 20, 40, 80):
                att = {k: b * extra.get(k, 1.0) for k in todos}
                att["_classe"] = classe
                dfn = {k: b for k in todos}

                rng = random.Random(3)
                amostras = [
                    golpe(att, dfn, acao, crit_extra=-1.0, rng=rng)[0]
                    for _ in range(4000)
                ]
                amostras = [x for x in amostras if x > 0]  # descarta esquivas
                novo = sum(amostras) / len(amostras)
                antigo = _golpe_antigo(att, dfn, acao)
                razao = novo / antigo
                razoes.append(razao)
                linha.append(f"b={b}: x{razao:.2f}")
            print(f"{classe:9}", " | ".join(linha))

        razoes.sort()
        print("mediana novo/antigo sem escala:", round(razoes[len(razoes) // 2], 2))
    finally:
        ESCALA_DANO = escala_anterior


if __name__ == "__main__":
    calibrar()
