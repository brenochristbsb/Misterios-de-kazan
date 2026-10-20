"""
atributos5.py — Ponte entre os 10 atributos antigos e os 5 Atributos Universais (Bloco 2) + golpe() do Bloco 7.

POR QUE UMA PONTE (e não apagar as colunas)
    O jogo inteiro (treino, itens, clã, pet, Dojo, cadastro, telas) ainda fala os 10 atributos antigos. Esta ponte
    CONVERTE na hora do combate, sem migração destrutiva e sem quebrar o index.html. Quando a interface passar a
    mostrar só os 5 atributos, basta preencher Player.atr_* com `converter_jogador()` e trocar a fonte em `universais()`.

CONVERSÃO (soma: nenhum ponto some, só muda de gaveta)
    STR = taijutsu + forca            DEX = destreza
    INT = ninjutsu + genjutsu + inteligencia + chakra_attr
    AGI = agilidade                   VIG = vigor + defesa

GOLPE (Bloco 7, mesma ordem do combate.py)
    1. Ataque Bruto = principal_da_classe*1.5 + secundario + dano_arma
    2. Mitigação    = 100 / (100 + VIG do alvo)         (Ninja Médico: ignora VIG)
    3. Elemental    = vantagem/desvantagem; Taijutsu sempre 1.0; Ninjutsu: vantagem vira 1.50
    4. Dano         = Bruto * Mitigação * Elemental * poder_do_jutsu * ESCALA_DANO * aleatório(0.95..1.05)
    Esquiva (AGI) e crítico (DEX) seguem como antes: já eram agilidade e destreza.
"""
from __future__ import annotations

import random
from typing import Optional

from combate import CLASSES

POOL = {
    "str": ("taijutsu", "forca"),
    "int": ("ninjutsu", "genjutsu", "inteligencia", "chakra_attr"),
    "agi": ("agilidade",),
    "dex": ("destreza",),
    "vig": ("vigor", "defesa"),
}

# Ajuste de balanceamento: a fórmula do Bloco 7 soma os pontos do pool e dá mais dano que a antiga.
# Valor calibrado (ver calibrar() abaixo) para o dano médio ficar parecido com o de antes. Mexa aqui para balancear.
ESCALA_DANO = 0.68

# Multiplicador extra por classe (1.0 = sem ajuste). O roteiro não dá números; fica aqui para você afinar.
MULT_CLASSE = {"taijutsu": 1.0, "ninjutsu": 1.0, "genjutsu": 0.75, "marionetista": 1.0, "ninja_medico": 1.0}

# Inimigos e golpes sem classe: o principal vem da ação (físico = STR; o resto = INT), secundário DEX.
GENERICO = {"fisico": "str", "ninjutsu": "int", "genjutsu": "int", "secreta": "int"}


def universais(stats: dict) -> dict:
    """Converte um dict com os 10 atributos antigos nos 5 universais."""
    return {k: sum(stats.get(a, 0) for a in attrs) for k, attrs in POOL.items()}


def classe_efetiva(p) -> str:
    """Classe escolhida; se ainda não escolheu, a do maior entre taijutsu/ninjutsu/genjutsu (o 'estilo' antigo)."""
    if getattr(p, "classe", None) in CLASSES:
        return p.classe
    estilos = {"taijutsu": p.taijutsu, "ninjutsu": p.ninjutsu, "genjutsu": p.genjutsu}
    return max(estilos, key=estilos.get)


def converter_jogador(p) -> None:
    """Grava os 5 atributos universais em Player.atr_* (para quando a interface passar a usá-los)."""
    u = universais({a: getattr(p, a) for attrs in POOL.values() for a in attrs})
    p.atr_str, p.atr_int, p.atr_agi, p.atr_dex, p.atr_vig = u["str"], u["int"], u["agi"], u["dex"], u["vig"]


def _princ_sec(att: dict, acao: str):
    classe = att.get("_classe")
    if classe in CLASSES:
        r = CLASSES[classe]
        return r["principal"], r["secundario"], classe
    return GENERICO.get(acao, "int"), "dex", None


def golpe(att: dict, dfn: dict, acao: str, poder: float = 1.0, mod: float = 1.0, crit_extra: float = 0.0,
          rng: Optional[random.Random] = None):
    """Substitui game_extra.golpe. Retorna (dano, crítico, atordoa); dano 0 = esquivou."""
    rng = rng or random
    # Esquiva: Agilidade do alvo contra a do atacante (igual à regra anterior)
    if rng.random() < 0.5 * dfn["agilidade"] / (dfn["agilidade"] + att["agilidade"] + 10):
        return 0, False, False

    a, d = universais(att), universais(dfn)
    princ, sec, classe = _princ_sec(att, acao)
    if classe == "taijutsu" and att.get("_portoes"):  # Portões Internos dobram STR e AGI brutos
        a["str"] *= 2
        a["agi"] *= 2

    # 1. Ataque Bruto
    bruto = a[princ] * 1.5 + a[sec] + att.get("_dano_arma", 0)

    # 2. Mitigação (Bisturi de Chakra do Ninja Médico ignora o Vigor)
    regra = CLASSES.get(classe, {})
    vig = 0 if regra.get("dano_verdadeiro") else d["vig"]
    mitig = 100 / (100 + vig)

    # 3. Modificador elemental (mod vem do ciclo de elementos: >1 vantagem, <1 desvantagem)
    if regra.get("ignora_elemento"):
        elem = 1.0
    elif mod > 1:
        elem = regra.get("bonus_elemental", 1.25)  # 1.25 padrão; Ninjutsu 1.50
    elif mod < 1:
        elem = 0.75
    else:
        elem = 1.0

    # 4. Dano do turno
    dano = bruto * mitig * elem * poder * ESCALA_DANO * MULT_CLASSE.get(classe, 1.0) * rng.uniform(0.95, 1.05)
    crit = rng.random() < att["destreza"] / (att["destreza"] + 60) + crit_extra
    if crit:
        dano *= 1.5
    return max(1, int(dano)), crit, acao == "genjutsu" and rng.random() < 0.3


# ───────── calibração: compara o dano antigo com o novo em vários níveis de poder ─────────
def _golpe_antigo(att, dfn, acao):
    if acao == "fisico":
        raw, red = att["taijutsu"] + att["forca"] * 0.8 + att["destreza"] * 0.4, dfn["defesa"] * 0.6
    elif acao == "ninjutsu":
        raw, red = att["ninjutsu"] * 1.3 + att["inteligencia"] * 0.5, dfn["defesa"] * 0.3
    else:
        raw, red = att["genjutsu"] + att["inteligencia"] * 0.8, dfn["inteligencia"] * 0.5
    return max(1, (raw - red) * 1.3)


def calibrar() -> None:
    """Imprime a razão novo/antigo por classe e por 'poder' (b = valor médio dos atributos). Ideal: perto de 1."""
    global ESCALA_DANO
    perfis = {"taijutsu": ("fisico", dict(taijutsu=1.5, forca=1.0)), "ninjutsu": ("ninjutsu", dict(ninjutsu=1.5, inteligencia=1.0)),
              "genjutsu": ("genjutsu", dict(genjutsu=1.5, inteligencia=1.0))}
    todos = ("taijutsu", "ninjutsu", "genjutsu", "agilidade", "forca", "inteligencia", "chakra_attr", "defesa", "vigor", "destreza")
    ant = ESCALA_DANO
    ESCALA_DANO = 1.0
    razoes = []
    for classe, (acao, extra) in perfis.items():
        linha = []
        for b in (5, 10, 20, 40, 80):
            att = {k: b * extra.get(k, 1.0) for k in todos}
            att["_classe"] = classe
            dfn = {k: b for k in todos}
            rng = random.Random(3)
            novo = sum(golpe(att, dfn, acao, rng=rng)[0] for _ in range(4000)) / 4000
            r = novo / _golpe_antigo(att, dfn, acao)
            razoes.append(r)
            linha.append(f"b={b}: x{r:.2f}")
        print(f"{classe:9}", " | ".join(linha))
    razoes.sort()
    print("mediana novo/antigo sem escala:", round(razoes[len(razoes) // 2], 2))
    ESCALA_DANO = ant


if __name__ == "__main__":
    calibrar()
