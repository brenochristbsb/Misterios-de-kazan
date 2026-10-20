"""
Smoke test de game_extra.py (luta_agir / turno_inimigo / hospital) com o Bloco 3.
fastapi, pydantic, sqlalchemy, models.py e os módulos de dados NÃO estavam no envio, então são substituídos por stubs
mínimos. Isso valida a lógica nova do combate, não o banco real. Rode também a suíte com o backend completo.
"""
import copy
import random
import sys
import types
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest

# ───── stubs ─────
class HTTPException(Exception):
    def __init__(self, status_code, detail=""):
        self.status_code, self.detail = status_code, detail
        super().__init__(detail)

fa = types.ModuleType("fastapi")
class _R:
    def get(self, *a, **k): return lambda f: f
    post = get
fa.APIRouter, fa.Depends, fa.HTTPException = _R, (lambda x=None: x), HTTPException
pd = types.ModuleType("pydantic")
class BaseModel:
    def __init__(self, **kw): self.__dict__.update({"item": None, **kw})
pd.BaseModel, pd.Field = BaseModel, (lambda *a, **k: None)
sys.modules.update({"fastapi": fa, "pydantic": pd})
for n in ("sqlalchemy", "sqlalchemy.orm", "models", "bloco4", "vip_passe", "vilas_mapas_extra", "clas_dados", "vilas_dados"):
    sys.modules[n] = MagicMock()
sys.modules["vilas_mapas_extra"].MAPAS_VILA_EXTRA, sys.modules["vilas_mapas_extra"].PRIMEIRO_MAPA_EXTRA = [], 100
sys.modules["clas_dados"].CLANS, sys.modules["vilas_dados"].VILAS = {}, []
jd = types.ModuleType("jutsus_dados")
jd.ELEMENTOS = {e: e for e in ("fogo", "agua", "vento", "raio", "terra")}
jd.BASICO = {"fisico": "Soco", "ninjutsu": {e: f"Jutsu de {e}" for e in jd.ELEMENTOS}, "genjutsu": "Ilusão"}
jd.JUTSUS, jd.MARCOS, jd.VANTAGEM = {}, [], {"fogo": "vento", "vento": "raio", "raio": "terra", "terra": "agua", "agua": "fogo"}
sys.modules["jutsus_dados"] = jd

ATTRS = ("taijutsu", "ninjutsu", "genjutsu", "agilidade", "forca", "inteligencia", "chakra_attr", "defesa", "vigor", "destreza")
PLAYERS = {}
api = types.ModuleType("api")
api.ATTRS, api.NO_CLAN_NAME = ATTRS, "sem clã"
api.active_summon_link = api.apply_exp = api.rank_titulo = MagicMock()
api.as_utc = lambda d: d
api.effective_stats = lambda p: dict(p.stats)
api.get_db = lambda: None
api.get_player = lambda db, tid, lock=False: PLAYERS[tid]
sys.modules["api"] = api

# combate / combate_estado: mesmos stubs do teste do atributos5
combate = types.ModuleType("combate")
combate.CLASSES = {"taijutsu": {"principal": "str", "secundario": "agi"}, "ninjutsu": {"principal": "int", "secundario": "dex", "bonus_elemental": 1.5},
                   "genjutsu": {"principal": "int", "secundario": "agi"}, "marionetista": {"principal": "int", "secundario": "dex"},
                   "ninja_medico": {"principal": "int", "secundario": "dex", "dano_verdadeiro": True}}
ce = types.ModuleType("combate_estado")
class EstadoLuta:
    def __init__(self, **k): self.__dict__.update(k)
    portoes_ativos = 0
ce.EstadoLuta, ce.acumular_furia, ce.absorver_dano_marionete = EstadoLuta, (lambda *a, **k: None), (lambda e, d: d)
ce.rolar_efeito_genjutsu, ce.iniciar_turno = (lambda e: None), (lambda e: None)
sys.modules.update({"combate": combate, "combate_estado": ce})

import atributos5  # noqa: E402
import classes_bloco3 as b3  # noqa: E402
import game_extra as g  # noqa: E402


def mk_player(classe, stats_base=20, **kw):
    p = NS(classe=classe, stats={a: stats_base for a in ATTRS}, vida_atual=100, vida_max=100, chakra_atual=200, chakra_max=200,
           marionete={}, passiva_medica="", elemento="fogo", status_fora=[], pocoes={}, moral=0, abates={}, tecnica="", jutsus={},
           luta=None, ryos=1000, level=20, hospital_gratis_at=None, buff=None, nk_fama=0, masmorra=None, torre_sessao=None, energia=10)
    for a in ATTRS: setattr(p, a, stats_base)
    p.__dict__.update(kw)
    return p


def mk_luta(boss=False, tipo="pve", hp=100000, foe=20, **extra):
    return {"tipo": tipo, "nome": "Inimigo", "boss": boss, "stats": {a: foe for a in ATTRS}, "hp": hp, "hp_max": hp, "chakra": 0,
            "turno": 0, "log": [], "defendendo": False, "atordoado": False, "me_atordoado": False, "pet": None, "pet_id": None,
            "meu_elem": "fogo", "elemento": "vento", "st": {}, "escudo": 0, "reserva": 0, **extra}


class DB:
    def commit(self): pass


def agir(p, acao, item=None):
    PLAYERS[1] = p
    return g.luta_agir(g.AgirReq(telegram_id=1, acao=acao, item=item), DB())


def com_marionete(p, **kw):
    u = atributos5.universais(p.stats)
    m = b3.normalizar(p.marionete, u)
    m.update(kw)
    p.marionete = m
    return m


# ───── marionete: proteção, destruição e contorno no turno do inimigo ─────
def test_turno_inimigo_marionete_absorve_e_jogador_nao_perde_vida():
    random.seed(1)
    p = mk_player("marionetista"); com_marionete(p, ativa=True)
    hp0, L = p.marionete["hp"], mk_luta()
    for _ in range(5):
        g.turno_inimigo(L, g._me(p, L), p)
    assert p.vida_atual == 100 and p.marionete["hp"] < hp0


def test_marionete_destruida_jogador_volta_a_receber_dano():
    random.seed(2)
    p = mk_player("marionetista"); com_marionete(p, ativa=True, hp=1)
    L = mk_luta(foe=60)
    for _ in range(6):
        g.turno_inimigo(L, g._me(p, L), p)
    assert p.marionete["hp"] == 0 and not p.marionete["ativa"] and p.vida_atual < 100


def test_boss_contorna_ou_corta_linhas_em_algum_momento_sem_imunidade_permanente():
    random.seed(3)
    p = mk_player("marionetista", stats_base=40); com_marionete(p, ativa=True)
    p.vida_max = p.vida_atual = 10_000
    L = mk_luta(boss=True, foe=30)
    for _ in range(60):
        g.turno_inimigo(L, g._me(p, L), p)
        p.marionete = {**p.marionete, "hp": p.marionete["hp_max"]}   # marionete "imortal" para isolar o contorno
        p.marionete = b3.passar_turno(p.marionete)
    assert p.vida_atual < 10_000


def test_classe_comum_nao_e_afetada_pela_marionete():
    random.seed(4)
    p = mk_player("ninjutsu"); L = mk_luta()
    for _ in range(5):
        g.turno_inimigo(L, g._me(p, L), p)
    assert p.vida_atual < 100


# ───── ações do Marionetista no luta_agir ─────
def test_invocar_gasta_chakra_e_ativa():
    random.seed(5)
    p = mk_player("marionetista"); p.luta = mk_luta()
    agir(p, "invocar")
    assert p.marionete["ativa"] and p.chakra_atual <= 200 - b3.MAR_INVOCAR_CUSTO


def test_acoes_exigem_marionete_ativa_e_classe():
    p = mk_player("marionetista"); p.luta = mk_luta()
    for a in ("agulhas", "veneno"):
        with pytest.raises(HTTPException):
            agir(p, a)
    q = mk_player("ninjutsu"); q.luta = mk_luta()
    for a in ("invocar", "agulhas", "veneno", "curar", "bisturi"):
        with pytest.raises(HTTPException):
            agir(q, a, "palma")


def test_veneno_aplica_e_tica_nos_turnos_seguintes():
    random.seed(6)
    p = mk_player("marionetista"); com_marionete(p, ativa=True); p.luta = mk_luta()
    for _ in range(3):
        agir(p, "veneno")
    L = p.luta
    assert L["veneno"] and L["veneno"]["stacks"] >= 1
    assert L["hp"] < 100000 - 1


def test_agulhas_causam_dano_e_custam_chakra():
    random.seed(7)
    p = mk_player("marionetista"); com_marionete(p, ativa=True); p.luta = mk_luta()
    c0 = p.chakra_atual
    agir(p, "agulhas")
    assert p.luta["hp"] < 100000 and p.chakra_atual < c0


def test_modulo_elemental_exige_instalacao_e_entra_em_recarga():
    random.seed(8)
    p = mk_player("marionetista"); com_marionete(p, ativa=True); p.luta = mk_luta()
    with pytest.raises(HTTPException):
        agir(p, "modulo", "fogo")
    com_marionete(p, modulos=["fogo", "fumaca"])
    agir(p, "modulo", "fogo")
    assert p.luta["hp"] < 100000 and p.marionete["cd"].get("fogo", 0) > 0
    with pytest.raises(HTTPException):
        agir(p, "modulo", "fogo")
    agir(p, "modulo", "fumaca")
    assert "cego" in p.luta["inim_st"]


# ───── Ninja Médico ─────
def test_cura_e_acao_livre_nao_gasta_turno_nem_faz_inimigo_agir():
    random.seed(9)
    p = mk_player("ninja_medico", vida_atual=40); p.luta = mk_luta()
    r = agir(p, "curar", "palma")
    assert p.vida_atual > 40 and p.luta["turno"] == 0 and r["resultado"] is None
    assert not any("Inimigo usou" in x for x in p.luta["log"])


def test_cura_nao_pode_ser_repetida_no_mesmo_turno_e_tem_teto_por_luta():
    random.seed(10)
    p = mk_player("ninja_medico", vida_atual=50_000, vida_max=100_000, chakra_atual=10_000, chakra_max=10_000); p.luta = mk_luta()
    agir(p, "curar", "palma")
    for item in ("palma", "selo"):
        with pytest.raises(HTTPException):
            agir(p, "curar", item)
    n = 1
    for _ in range(20):
        p.vida_atual = 50_000
        agir(p, "bisturi")          # avança o turno
        try:
            agir(p, "curar", "palma"); n += 1
        except HTTPException:
            break
    assert n == b3.MAX_CURAS_LUTA


def test_cura_valida_chakra_e_vida_cheia():
    p = mk_player("ninja_medico", vida_atual=40, chakra_atual=5); p.luta = mk_luta()
    with pytest.raises(HTTPException):
        agir(p, "curar", "palma")
    p.chakra_atual, p.vida_atual = 200, 100
    with pytest.raises(HTTPException):
        agir(p, "curar", "palma")
    with pytest.raises(HTTPException):
        agir(p, "curar", "../../x")


def test_bisturi_ignora_vigor_do_alvo_e_respeita_teto():
    random.seed(11)
    p = mk_player("ninja_medico", stats_base=30); p.luta = mk_luta(foe=1)
    p.luta["stats"].update(vigor=900, defesa=900)
    hp0 = p.luta["hp"]
    agir(p, "bisturi")
    dano_bisturi = hp0 - p.luta["hp"]
    p2 = mk_player("ninja_medico", stats_base=30); p2.luta = mk_luta(foe=1)
    p2.luta["stats"].update(vigor=900, defesa=900)
    agir(p2, "fisico")
    dano_comum = hp0 - p2.luta["hp"]
    assert dano_bisturi > dano_comum * 3
    p3 = mk_player("ninja_medico", stats_base=500); p3.luta = mk_luta(foe=1, hp=400)
    agir(p3, "bisturi")
    assert 400 - p3.luta["hp"] <= int(400 * b3.BISTURI_TETO_PCT)


# ───── PvP: marionete do rival e contrajogada ─────
def test_pvp_marionete_do_rival_absorve_e_cortar_linhas_contorna():
    random.seed(12)
    p = mk_player("ninjutsu"); L = mk_luta(tipo="pvp", foe=20)
    L["stats"]["_classe"] = "marionetista"
    mf = b3.normalizar({}, atributos5.universais(L["stats"])); mf["ativa"] = True
    L["mar_foe"], p.luta = mf, L
    agir(p, "fisico")
    assert p.luta["hp"] == 100000 and p.luta["mar_foe"]["hp"] < mf["hp"]       # absorveu tudo
    for _ in range(20):                                                        # chance de falhar o corte
        agir(p, "cortar_linhas")
        if p.luta["mar_foe"]["suspensa"]:
            break
    assert p.luta["mar_foe"]["suspensa"] > 0
    L2 = p.luta; hp_antes = L2["hp"]
    agir(p, "fisico")
    assert p.luta["hp"] < hp_antes


# ───── hospital: passivas ─────
def test_hospital_info_passivas_do_medico():
    base = mk_player("ninja_medico"); base.hospital_gratis_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    cd0 = g.hospital_info(base)["gratis_restante_s"]
    hosp = mk_player("ninja_medico", passiva_medica="hospital", hospital_gratis_at=base.hospital_gratis_at)
    assert 295 <= g.hospital_info(hosp)["gratis_restante_s"] <= 300 and 595 <= cd0 <= 600
    trat = mk_player("ninja_medico", passiva_medica="tratamento")
    assert g.hospital_info(trat)["preco_total"] == round(0.7 * g.hospital_info(base)["preco_total"])
    outro = mk_player("ninjutsu", passiva_medica="tratamento")
    assert g.hospital_info(outro)["preco_total"] == g.hospital_info(base)["preco_total"]
