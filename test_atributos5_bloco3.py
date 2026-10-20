"""
Testes da integração do Bloco 3 em atributos5.golpe (dano verdadeiro do Bisturi, bônus elemental do Marionetista).
combate.py e combate_estado.py reais não estavam no envio: os stubs abaixo imitam só o formato que atributos5.py usa
(CLASSES[...]["principal"|"secundario"|"dano_verdadeiro"|"ignora_elemento"|"bonus_elemental"]). Se os reais divergirem, ajuste os stubs.
"""
import dataclasses
import random
import sys
import types

import pytest

combate = types.ModuleType("combate")
combate.CLASSES = {
    "taijutsu": {"principal": "str", "secundario": "agi"},
    "ninjutsu": {"principal": "int", "secundario": "dex", "bonus_elemental": 1.5},
    "genjutsu": {"principal": "int", "secundario": "agi"},
    "marionetista": {"principal": "int", "secundario": "dex"},
    "ninja_medico": {"principal": "int", "secundario": "dex", "dano_verdadeiro": True},
}
estado = types.ModuleType("combate_estado")


@dataclasses.dataclass
class EstadoLuta:
    classe_atacante: str = ""
    classe_defensor: object = None
    rng: object = None
    portoes_ativos: int = 0


estado.EstadoLuta = EstadoLuta
estado.acumular_furia = lambda *a, **k: None
estado.absorver_dano_marionete = lambda e, d: d
estado.rolar_efeito_genjutsu = lambda e: None
estado.iniciar_turno = lambda e: None
sys.modules.setdefault("combate", combate)
sys.modules.setdefault("combate_estado", estado)
import atributos5  # noqa: E402

ATTRS = ("taijutsu", "ninjutsu", "genjutsu", "agilidade", "forca", "inteligencia", "chakra_attr", "defesa", "vigor", "destreza")


def stats(base, classe, **extra):
    s = {k: base for k in ATTRS}
    s.update(extra)
    s["_classe"] = classe
    return s


def media(att, dfn, n=600, **kw):
    r = random.Random(7)
    xs = [atributos5.golpe(att, dfn, "fisico", crit_extra=-1.0, rng=r, **kw)[0] for _ in range(n)]
    xs = [x for x in xs if x > 0]
    return sum(xs) / len(xs)


def test_bisturi_ignora_vigor_e_ataque_comum_do_medico_nao():
    att = stats(20, "ninja_medico")
    tanque = stats(20, "taijutsu", vigor=400, defesa=400)
    comum = media(att, tanque)
    bisturi = media(att, tanque, dano_verdadeiro=True)
    assert bisturi > comum * 5          # o Vigor 800 corta o golpe comum; o Bisturi ignora


def test_bisturi_nao_depende_do_vigor_do_alvo():
    att = stats(20, "ninja_medico")
    a = media(att, stats(20, "taijutsu", vigor=0, defesa=0), dano_verdadeiro=True)
    b = media(att, stats(20, "taijutsu", vigor=900, defesa=900), dano_verdadeiro=True)
    assert abs(a - b) / a < 0.02        # sem redução, nem duplicada


def test_flag_antiga_devolve_ignorar_vigor_em_todo_ataque():
    att, alvo = stats(20, "ninja_medico"), stats(20, "taijutsu", vigor=300, defesa=300)
    atributos5.MEDICO_BASICO_VERDADEIRO = True
    try:
        assert media(att, alvo) > media(att, alvo, dano_verdadeiro=False) * 3
    finally:
        atributos5.MEDICO_BASICO_VERDADEIRO = False


def test_dano_verdadeiro_nao_ignora_esquiva():
    att = stats(20, "ninja_medico")
    agil = stats(20, "taijutsu", agilidade=5000)
    lento = stats(20, "taijutsu", agilidade=1)
    r = random.Random(3)
    zeros = sum(atributos5.golpe(att, agil, "fisico", rng=r, dano_verdadeiro=True)[0] == 0 for _ in range(400))
    zeros_lento = sum(atributos5.golpe(att, lento, "fisico", rng=r, dano_verdadeiro=True)[0] == 0 for _ in range(400))
    assert zeros > 150 and zeros_lento < 60   # esquiva (teto de 50%) continua valendo contra dano verdadeiro


def test_marionetista_nao_recebe_bonus_elemental_exclusivo_do_ninjutsu():
    alvo = stats(20, "taijutsu")
    def com_vantagem(classe):
        att = stats(20, classe)
        r = random.Random(5)
        return sum(atributos5.golpe(att, alvo, "ninjutsu", mod=1.3, crit_extra=-1.0, rng=r)[0] for _ in range(300))
    def neutro(classe):
        att = stats(20, classe)
        r = random.Random(5)
        return sum(atributos5.golpe(att, alvo, "ninjutsu", mod=1.0, crit_extra=-1.0, rng=r)[0] for _ in range(300))
    assert abs(com_vantagem("marionetista") / neutro("marionetista") - 1.25) < 0.05
    assert abs(com_vantagem("ninjutsu") / neutro("ninjutsu") - 1.5) < 0.05
