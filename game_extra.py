"""
game_extra.py — Mapas com batalha PvE, drops, inventário, invocações, clãs e reputação.

Este módulo é registrado no final do api.py (app.include_router).
Mantenha api.py, models.py e game_extra.py na mesma pasta.
"""

import copy
import random
from datetime import date, datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from clas_dados import CLANS
from vilas_dados import VILAS
from jutsus_dados import BASICO, ELEMENTOS, JUTSUS, MARCOS, VANTAGEM
from vilas_mapas_extra import MAPAS_VILA_EXTRA, PRIMEIRO_MAPA_EXTRA
from sqlalchemy import func, select
from sqlalchemy.orm import Session, object_session

from api import ATTRS, NO_CLAN_NAME, active_summon_link, apply_exp, as_utc, effective_stats, get_db, get_player, rank_titulo
import atributos5
import bloco4
from vip_passe import multiplicador_ryos, multiplicador_xp  # bônus do VIP (Shinobi/Shogun)
from models import BossThreat, Clan, Guild, InventoryItem, ItemType, MarketListing, NinjaRank, Pet, Player, PlayerSummon, Summon, Village

router = APIRouter()


def prog(p, ev: str, n: int = 1) -> None:
    """Avisa o módulo de progresso (missões, conquistas, passe) que algo aconteceu. Nunca atrapalha a ação original."""
    try:
        from progresso_extra import registrar
        registrar(p, ev, n)
    except Exception:
        pass

RANKS = list(NinjaRank)  # Estudante -> Kage
EXPLORE_CHAKRA = 15
CLAN_TEST_CHAKRA = 20
REST_COST = 50
AVATARS = ["🥷", "🦊", "🐺", "🐉", "🦅", "🐍", "🐻", "🐯", "🦉", "🐸", "🌙", "🔥"]

# Mapas: faixa de nível ideal (min a max), tema de drop (tipo) e itens possíveis, cada um com o atributo que reforça.
# Acima do nível máximo o mapa rende só 25% do XP: é hora de seguir para o próximo.
_RAW_MAPS = [
    (1, "Floresta do Treino", 1, 10, 1, "Arma", [("Kunai de Ferro", "taijutsu"), ("Shuriken Leve", "agilidade")], "Lobo Alfa"),
    (2, "Vale dos Bambus", 5, 16, 2, "Pergaminho", [("Pergaminho de Selos", "ninjutsu"), ("Pergaminho de Ilusão", "genjutsu")], "Mestre dos Bambus"),
    (3, "Cavernas do Eco", 10, 22, 3, "Armadura", [("Colete de Couro Negro", "agilidade"), ("Manto de Cristal", "genjutsu")], "Aranha Cristalina"),
    (4, "Pântano Sombrio", 16, 30, 4, "Arma", [("Katana Sombria", "taijutsu"), ("Lâmina Corrosiva", "ninjutsu")], "Guardião do Pântano"),
    (5, "Picos Gelados", 24, 40, 5, "Armadura", [("Traje Glacial", "agilidade"), ("Elmo de Geada", "taijutsu")], "Colosso de Gelo"),
]
MAPS = [{"id": i, "nome": n, "min_level": a, "max_level": b, "perigo": p, "tipo": t, "itens": it,
         "inimigo": "Ninja da região", "chefe": c} for i, n, a, b, p, t, it, c in _RAW_MAPS]

# ───────── Fase 3: mais inimigos e drops nos mapas 1 a 5, e mapas 6 a 15 (até o nível 100) ─────────
# Itens: (nome, atributo) usam o tipo do mapa; (nome, atributo, tipo) sobrescrevem o tipo.
_EXTRAS_ANTIGOS = {1: (["Bandido Errante", "Javali Selvagem"], ("Botas Leves de Treino", "agilidade", "Botas")),
                   2: (["Ninja Desertor", "Macaco Ágil"], ("Anel de Bambu", "genjutsu", "Anel")),
                   3: (["Morcego Gigante", "Mineiro Corrompido"], ("Luvas de Cristal", "taijutsu", "Luvas")),
                   4: (["Sapo Venenoso", "Caçador de Pântano"], ("Colar do Pântano", "ninjutsu", "Colar")),
                   5: (["Lobo da Geada", "Eremita Gélido"], ("Botas de Gelo", "agilidade", "Botas"))}
for _m in MAPS:
    _m["extras"], _it = _EXTRAS_ANTIGOS[_m["id"]]
    _m["itens"] = _m["itens"] + [_it]
    _m["recurso"] = None  # nos mapas 1 a 5 o recurso de coleta vem da vila (vilas_dados.py)

# (id, nome, nível mín., nível máx., perigo, tipo, itens, chefe, inimigo, outros inimigos, recurso de coleta)
_NOVOS_MAPAS = [
    (6, "Ruínas Esquecidas", 32, 46, 6, "Botas", [("Botas de Musgo", "agilidade"), ("Sandálias Rúnicas", "taijutsu"), ("Colar de Runas", "genjutsu", "Colar")],
     "Sentinela de Pedra", "Saqueador das Ruínas", ["Golem Menor", "Espectro de Pedra"], "Fragmento Rúnico"),
    (7, "Floresta Carmesim", 40, 54, 7, "Luvas", [("Luvas de Espinho", "taijutsu"), ("Garras Carmesim", "agilidade"), ("Anel de Seiva", "ninjutsu", "Anel")],
     "Rainha das Vespas", "Caçador Carmesim", ["Vespa Gigante", "Lobo Sangrento"], "Seiva Carmesim"),
    (8, "Deserto das Dunas", 48, 62, 8, "Colar", [("Colar de Areia Vítrea", "ninjutsu"), ("Amuleto do Oásis", "genjutsu"), ("Manto das Dunas", "agilidade", "Armadura")],
     "Verme das Dunas", "Nômade Renegado", ["Escorpião Colossal", "Bandido do Deserto"], "Cristal de Areia"),
    (9, "Costa das Tempestades", 56, 68, 9, "Arma", [("Tridente de Maré", "taijutsu"), ("Lâmina de Coral", "ninjutsu"), ("Botas de Espuma", "agilidade", "Botas")],
     "Leviatã Menor", "Pirata Shinobi", ["Caranguejo de Aço", "Sereia Sombria"], "Pérola Tempestuosa"),
    (10, "Vulcão Adormecido", 64, 76, 10, "Armadura", [("Armadura de Magma", "taijutsu"), ("Traje de Cinzas", "agilidade"), ("Anel Incandescente", "ninjutsu", "Anel")],
     "Titã de Lava", "Cultista das Cinzas", ["Salamandra de Fogo", "Golem de Magma"], "Rubi Vulcânico"),
    (11, "Catacumbas Antigas", 72, 84, 11, "Pergaminho", [("Pergaminho Proibido", "genjutsu"), ("Grimório dos Mortos", "ninjutsu"), ("Colar de Ossos", "taijutsu", "Colar")],
     "Rei Lich", "Necromante", ["Esqueleto Guerreiro", "Fantasma Errante"], "Osso Ancestral"),
    (12, "Montanha das Tempestades", 78, 90, 12, "Luvas", [("Manoplas do Trovão", "taijutsu"), ("Luvas de Raio", "ninjutsu"), ("Botas do Vendaval", "agilidade", "Botas")],
     "Dragão do Trovão", "Xamã do Vento", ["Águia Relâmpago", "Elemental de Raio"], "Pedra do Trovão"),
    (13, "Templo Celestial", 84, 94, 13, "Anel", [("Anel Celestial", "genjutsu"), ("Selo do Templo", "ninjutsu"), ("Manto de Seda Divina", "agilidade", "Armadura")],
     "Guardião Celestial", "Monge Corrompido", ["Estátua Viva", "Anjo Caído"], "Pétala Celestial"),
    (14, "Abismo Sombrio", 90, 98, 14, "Arma", [("Lâmina do Abismo", "taijutsu"), ("Foice das Sombras", "ninjutsu"), ("Colar do Vazio", "genjutsu", "Colar")],
     "Devorador de Almas", "Assassino das Sombras", ["Sombra Viva", "Demônio Menor"], "Essência Sombria"),
    (15, "Portal do Fim", 94, 100, 15, "Armadura", [("Armadura do Fim", "taijutsu"), ("Anel do Vazio Eterno", "genjutsu", "Anel"),
                                                    ("Pergaminho do Apocalipse", "ninjutsu", "Pergaminho"), ("Luvas do Destino", "taijutsu", "Luvas")],
     "Senhor do Fim", "Arauto do Fim", ["Besta do Vazio", "Cavaleiro Espectral"], "Fragmento do Vazio"),
]
MAPS += [{"id": i, "nome": n, "min_level": a, "max_level": b, "perigo": p, "tipo": t, "itens": it, "inimigo": ini, "chefe": c,
          "extras": ex, "recurso": rec} for i, n, a, b, p, t, it, c, ini, ex, rec in _NOVOS_MAPAS]
MAPS_BY_ID = {m["id"]: m for m in MAPS}
vila_idx = lambda p: (p.village_id - 1) % len(VILAS)  # as vilas são semeadas na ordem de vilas_dados.py


def mapa_vila(mid: int, vi: int) -> dict:
    """Mapa do nível `mid` com nome, inimigo e chefe da vila: a dificuldade é igual, só a ambientação muda."""
    mp, lista = MAPS_BY_ID[mid], VILAS[vi]["mapas"]
    if mid > len(lista):  # mapas 6 a 15: nomes por vila em vilas_mapas_extra.py (sem entrada, usa o nome genérico)
        ex = MAPAS_VILA_EXTRA.get(vi, [])
        if 0 <= mid - PRIMEIRO_MAPA_EXTRA < len(ex):
            nome, inimigo, chefe, _ = ex[mid - PRIMEIRO_MAPA_EXTRA]
            return {**mp, "nome": nome, "inimigo": inimigo, "chefe": chefe}
        return dict(mp)
    nome, inimigo, chefe = lista[mid - 1]
    return {**mp, "nome": nome, "inimigo": inimigo, "chefe": chefe}


def recurso_vila(vi: int, mid: int) -> str:
    rec = VILAS[vi]["recursos"]
    if mid <= len(rec):
        return rec[mid - 1]
    ex = MAPAS_VILA_EXTRA.get(vi, [])
    return ex[mid - PRIMEIRO_MAPA_EXTRA][3] if 0 <= mid - PRIMEIRO_MAPA_EXTRA < len(ex) else MAPS_BY_ID[mid]["recurso"]

# Requisitos de ingresso por clã (nível, rank). Clãs fora da lista usam o padrão.
CLAN_DATA = {c[1]: {"vila": c[0], "nome": c[1], "atr": c[2], "bonus": c[3], "nivel": c[4], "rank": c[5], "historia": c[6],
                    "etapas": c[7].split(), "lider": c[8], "tecnica": c[9], "reliquia": c[10], "tipo": c[11],
                    "rel_bonus": c[12]} for c in CLANS}
for _d in CLAN_DATA.values():  # o clã nunca exige um mapa que o jogador ainda não pode explorar
    for _t in _d["etapas"]:
        if _t[0] in "kb":
            _d["nivel"] = max(_d["nivel"], MAPS_BY_ID[int(_t[1:].split("x")[0])]["min_level"])
clan_req = lambda c: (CLAN_DATA[c.name]["nivel"], CLAN_DATA[c.name]["rank"]) if c.name in CLAN_DATA else (5, "Genin")


def rank_ok(player, min_rank: str) -> bool:
    return RANKS.index(player.rank) >= RANKS.index(NinjaRank(min_rank))


# Escada de patentes. Jonin e Anbu têm um degrau de elite; Sannin e Kage vêm de fama, não de exame.
PATENTES = ["Estudante", "Genin", "Chunin", "Jonin", "Jonin de Elite", "Anbu", "Anbu de Elite", "Sannin", "Kage"]
posto = lambda p: PATENTES.index(rank_titulo(p))


def promover(p: Player, alvo: str) -> None:
    p.rank, p.elite = NinjaRank(alvo.replace(" de Elite", "")), alvo.endswith(" de Elite")


def reputation_title(rep: int) -> str:
    return "Herói da Vila" if rep >= 50 else "Renegado (Nukenin)" if rep <= -50 else "Shinobi neutro"


# ───────── Mecânica de batalha ─────────
def _hit(att: dict, dfn: dict) -> int:
    """Dano de um golpe. 0 = esquivado. Agilidade do defensor aumenta a esquiva."""
    dodge = 0.6 * dfn["agilidade"] / (dfn["agilidade"] + att["agilidade"] + 10)
    if random.random() < dodge:
        return 0
    base = att["taijutsu"] + att["ninjutsu"] + att["genjutsu"] * 0.5
    return max(1, int(base * random.uniform(0.8, 1.2)))


def duel(me: dict, my_hp: int, foe: dict, foe_hp: int, chakra: int = 0, max_rounds: int = 15):
    """
    Duelo por turnos (quem tem mais agilidade começa). O chakra só é gasto aqui: com 3 ou mais,
    o jogador usa uma técnica (golpe 50% mais forte). Retorna (venceu, vida_restante, log, chakra_restante).
    """
    log = []
    my_first = me["agilidade"] >= foe["agilidade"]
    for rd in range(1, max_rounds + 1):
        for mine in ((True, False) if my_first else (False, True)):
            att, dfn = (me, foe) if mine else (foe, me)
            dmg, tech = _hit(att, dfn), False
            if mine and chakra >= 3:
                chakra, tech, dmg = chakra - 3, True, int(dmg * 1.5)
            if mine:
                foe_hp -= dmg
            else:
                my_hp -= dmg
            who = "Você" if mine else "Inimigo"
            log.append(f"Rodada {rd}: {who} " + ((f"usou uma técnica e causou {dmg}" if tech else f"causou {dmg} de dano") if dmg else "errou o golpe"))
            if foe_hp <= 0 or my_hp <= 0:
                break
        if foe_hp <= 0 or my_hp <= 0:
            break
    return my_hp > foe_hp, max(my_hp, 1), log, chakra


# ───────── Schemas ─────────
class ExploreReq(BaseModel):
    telegram_id: int = Field(gt=0)
    map_id: int = Field(gt=0)


class EquipReq(BaseModel):
    telegram_id: int = Field(gt=0)
    item_id: int = Field(gt=0)
    equipar: bool = True


class SummonReq(BaseModel):
    telegram_id: int = Field(gt=0)
    summon_id: int = Field(gt=0)


class ClanReq(BaseModel):
    telegram_id: int = Field(gt=0)
    clan_id: int = Field(gt=0)


class RestReq(BaseModel):
    telegram_id: int = Field(gt=0)


# ───────── Rotas ─────────
@router.get("/api/player/{telegram_id}/extras")
def extras(telegram_id: int, db: Session = Depends(get_db)):
    """Dados complementares do perfil: reputação, inventário, invocações, mapas e clãs."""
    p = get_player(db, telegram_id)
    clans = db.scalars(select(Clan).where(Clan.name != NO_CLAN_NAME).order_by(Clan.name)).all()
    return {
        "avatares": AVATARS,
        "patente": patente_info(p),
        "hospital": hospital_info(p),
        "jutsus": jutsus_info(p),
        "reputacao": p.reputation,
        "titulo": reputation_title(p.reputation),
        "inventario": [{"id": i.id, "nome": i.item_name, "tipo": i.item_type.value,
                        "bonus": i.status_bonus or {}, "equipado": i.is_equipped} for i in p.inventory],
        "invocacoes": [{"id": ps.summon_id, "nome": ps.summon.name, "nivel": ps.level, "ativo": ps.is_active,
                        "bonus_taijutsu": ps.summon.bonus_taijutsu, "bonus_ninjutsu": ps.summon.bonus_ninjutsu}
                       for ps in p.player_summons],
        "moral": p.moral, "torre_sessao": bool(p.torre_sessao), "status": list(p.status_fora or []), "trofeus": p.trofeus or 0, "moedas_arena": p.moedas_arena or 0,
        "mapas": [{"id": m["id"], "nome": m["nome"], "min_level": m["min_level"], "max_level": m["max_level"],
                   "perigo": m["perigo"], "tipo": m["tipo"], "chefe": m["chefe"],
                   "itens": [i[0] for i in m["itens"]]} for m in [mapa_vila(x["id"], vila_idx(p)) for x in MAPS]],
        "clas": [{"id": c.id, "nome": c.name, "passiva": c.passiva_atributo.value, "bonus": c.bonus_val,
                  "min_level": clan_req(c)[0], "min_rank": clan_req(c)[1]} for c in clans],
    }


# ───────── Combate por turnos ─────────
# Uma só fórmula para jogador, monstro e PvP: quem tem os melhores atributos leva vantagem, sem bônus escondidos.
NOME_ACAO = {"fisico": "um golpe físico", "ninjutsu": "um ninjutsu", "genjutsu": "um genjutsu", "secreta": "sua técnica secreta"}


class AgirReq(BaseModel):
    telegram_id: int = Field(gt=0)
    acao: Literal["fisico", "ninjutsu", "genjutsu", "jutsu", "secreta", "pocao", "defender", "fugir"]
    item: Optional[str] = None  # id da poção, quando acao = "pocao"


class PvpReq(BaseModel):
    telegram_id: int = Field(gt=0)
    alvo: str = Field(min_length=3, max_length=32)


def custo_tecnica(s: dict) -> int:
    """Chakra por técnica: a inteligência melhora o controle e barateia o custo."""
    return max(4, 12 - s["inteligencia"] // 3)


def mod_elem(atq, defe) -> float:
    """Vantagem elemental: +30% contra o elemento mais fraco, -25% contra o que o vence."""
    if not atq or not defe:
        return 1.0
    if VANTAGEM.get(atq) == defe:
        return 1.3
    return 0.75 if VANTAGEM.get(defe) == atq else 1.0


USAR_BLOCO7 = True  # False volta à fórmula antiga de dano (golpe_legado)


def golpe(att: dict, dfn: dict, acao: str, poder: float = 1.0, mod: float = 1.0, crit_extra: float = 0.0):
    """Retorna (dano, crítico, atordoa). Dano 0 = esquivou. `poder` é o multiplicador do jutsu e `mod` o elemental.
    Usa a fórmula do Bloco 7 (atributos5.golpe), com os 10 atributos antigos convertidos nos 5 universais."""
    if USAR_BLOCO7:
        return atributos5.golpe(att, dfn, acao, poder, mod, crit_extra)
    return golpe_legado(att, dfn, acao, poder, mod, crit_extra)


def golpe_legado(att: dict, dfn: dict, acao: str, poder: float = 1.0, mod: float = 1.0, crit_extra: float = 0.0):
    """Fórmula antiga (10 atributos), mantida para comparação e para desligar o Bloco 7 se precisar."""
    if random.random() < 0.5 * dfn["agilidade"] / (dfn["agilidade"] + att["agilidade"] + 10):
        return 0, False, False
    if acao == "fisico":
        raw, red = att["taijutsu"] + att["forca"] * 0.8 + att["destreza"] * 0.4, dfn["defesa"] * 0.6
    elif acao == "secreta":  # técnica secreta do líder do clã: golpe devastador que quase ignora a defesa
        raw, red = max(att["ninjutsu"], att["genjutsu"]) * 2.2 + att["inteligencia"] * 0.8, dfn["defesa"] * 0.2
    elif acao == "ninjutsu":
        raw, red = att["ninjutsu"] * 1.3 + att["inteligencia"] * 0.5, dfn["defesa"] * 0.3
    else:  # genjutsu: a defesa mental é a inteligência
        raw, red = att["genjutsu"] + att["inteligencia"] * 0.8, dfn["inteligencia"] * 0.5
    dmg = (raw - red) * 1.3 * poder * mod * random.uniform(0.85, 1.15)
    crit = random.random() < att["destreza"] / (att["destreza"] + 60) + crit_extra
    if crit:
        dmg *= 1.5
    return max(1, int(dmg)), crit, acao == "genjutsu" and random.random() < 0.3


# ───────── Fase 2: dificuldade, status, moral e estrelas ─────────
# Ajuste de balanceamento: (fator dos atributos do inimigo, fator da vida, fator das recompensas) por dificuldade
DIFICULDADES = {"fácil": (0.70, 0.7, 0.6), "normal": (0.95, 1.0, 1.0), "difícil": (1.15, 1.3, 1.4), "desafiante": (1.35, 1.6, 1.8),
                "mini-chefe": (1.5, 2.0, 2.5), "chefe": (1.8, 2.8, 3.5), "impossível": (2.6, 4.5, 6.0)}
MORAL_MAX, ABATES_RAPIDO = 25, 30
STATUS_INFO = {"queimado": "🔥", "sangrando": "🩸", "congelado": "🧊", "paralisado": "⚡", "atordoado": "💫", "cego": "🕶️"}
STATUS_TURNOS = {"queimado": 3, "sangrando": 3, "congelado": 1, "paralisado": 2, "cego": 3}
ELEM_STATUS = {"fogo": "queimado", "agua": "congelado", "raio": "paralisado", "vento": "sangrando", "terra": "cego"}
ESTRELA_BONUS = {1: 1.25, 2: 1.5, 3: 2.0}


def dif_por_nivel(p: Player, nivel: int) -> str:
    """Dificuldade pela distância entre o nível do inimigo e o do jogador (mesma régua de antes)."""
    d = nivel - p.level
    return "fácil" if d <= -3 else "normal" if d <= 1 else "difícil" if d <= 3 else "desafiante"


def rolar_drops_exploracao(db: Session, p: Player, mp: dict) -> list:
    """Bloco 4: dois rolos independentes por clique em Explorar. Devolve os textos dos drops para a tela.
    Rolo A: materiais (o raro é do tier acima). Rolo B: fragmento de mapa ou equipamento comum/incomum/raro do mapa
    (raridade vira estrelas: incomum = 1★, raro = 2★). Equipamento sem espaço na mochila se perde."""
    out = []
    tier = int(mat_do_mapa(mp["perigo"]).split("_")[1])
    m = bloco4.rolar_materiais()
    if m:
        tipo, qtd = m
        mid = f"mat_{tier if tipo == 'basico' else min(5, tier + 1)}"
        est = dict(p.materiais or {})
        est[mid] = est.get(mid, 0) + qtd
        p.materiais = est
        out.append(f"{qtd}x {MAT_NOME[mid]}" + (" (raro)" if tipo == "raro" else ""))
    e = bloco4.rolar_equipamento()
    if e == "fragmento":
        k = f"frag_{mp['id']}"
        p.materiais = {**(p.materiais or {}), k: (p.materiais or {}).get(k, 0) + 1}
        out.append(frag_nome(mp["id"], vila_idx(p)))
    elif e:
        if bolsa_livre(p):
            nome, attr, *t = random.choice(mp["itens"])
            out.append(criar_item(db, p, mp, nome, attr, t[0] if t else mp["tipo"], {"comum": 0, "incomum": 1, "raro": 2}[e])["nome"])
        else:
            out.append("um equipamento (mochila cheia, perdido)")
    return out


def sortear_inimigo(p: Player, mp: dict, chefe_forcado: bool = False):
    """Escolhe dificuldade e nível do inimigo: variam dentro da faixa do mapa; chefes ficam no topo dela."""
    lo, hi, r = mp["min_level"], mp["max_level"], random.random()
    if chefe_forcado or r < 0.07:
        return "chefe", hi + random.randint(0, 1)
    if r < 0.075:
        return "impossível", hi + random.randint(3, 5)
    if r < 0.155:
        return "mini-chefe", max(lo, hi - random.randint(0, 2))
    nivel = random.randint(lo, hi)
    d = nivel - p.level
    return ("fácil" if d <= -3 else "normal" if d <= 1 else "difícil" if d <= 3 else "desafiante"), nivel


def stats_inimigo(p: Player, nivel: int, dif: str):
    """Atributos, vida e chakra do inimigo: metade vem do nível dele e metade dos pontos de status do jogador, vezes a dificuldade."""
    me = effective_stats(p)
    media = sum(me[a] for a in ATTRS) / len(ATTRS)
    ratio, hpm, _ = DIFICULDADES[dif]
    poder = max(3, round((0.5 * (4 + 0.55 * nivel) + 0.5 * media) * ratio))
    return {a: poder for a in ATTRS}, int((45 + 9 * nivel) * hpm), 40 + 3 * nivel


def aplicar_status(L: dict, nome: str) -> None:
    if nome == "atordoado":
        L["me_atordoado"] = True
    else:
        L.setdefault("st", {})[nome] = STATUS_TURNOS[nome]
    L["log"].append(f"Você ficou {nome}! {STATUS_INFO[nome]}")


def efeitos_turno(p: Player, L: dict) -> bool:
    """Aplica os status no início do turno do jogador. Retorna True se ele perde o turno."""
    st, perdeu = L.setdefault("st", {}), False
    for nome in list(st):
        if nome in ("queimado", "sangrando"):
            dano = max(2, int(p.vida_max * (0.04 if nome == "queimado" else 0.03)))
            p.vida_atual = max(0, p.vida_atual - dano)
            L["log"].append(f"{STATUS_INFO[nome]} Você sofre {dano} de dano por estar {nome}.")
        elif nome == "congelado":
            perdeu = True
            L["log"].append(f"{STATUS_INFO[nome]} Você está congelado e perde o turno.")
        elif nome == "paralisado" and random.random() < 0.45:
            perdeu = True
            L["log"].append(f"{STATUS_INFO[nome]} Você está paralisado e não consegue agir.")
        st[nome] -= 1
        if st[nome] <= 0:
            del st[nome]
            L["log"].append(f"Você não está mais {nome}.")
    return perdeu


def sortear_estrelas(moral: int) -> int:
    """Itens superiores (1 a 3 estrelas) são raros; a moral multiplica a chance em até 3x."""
    r, f = random.random(), 1 + moral / (MORAL_MAX / 2)
    return 3 if r < 0.001 * f else 2 if r < 0.005 * f else 1 if r < 0.02 * f else 0


# (poção, chance por vitória, perigo mínimo do mapa)
CONSUMIVEIS_DROP = [("antidoto", 0.08, 1), ("vida_p", 0.06, 1), ("chakra_p", 0.05, 1), ("energia", 0.008, 2),
                    ("vida_m", 0.04, 3), ("chakra_m", 0.03, 3), ("vida_g", 0.03, 8), ("chakra_g", 0.025, 8)]
# Eventos ao explorar (chance de cada um): curandeiro e tesouro resolvem na hora; a emboscada vira uma luta mais dura
EVENTOS = (("curandeiro", 0.05), ("tesouro", 0.05), ("emboscada", 0.08))


def sortear_evento() -> Optional[str]:
    r, acc = random.random(), 0.0
    for nome, chance in EVENTOS:
        acc += chance
        if r < acc:
            return nome
    return None


def criar_item(db: Session, p: Player, mp: dict, nome: str, attr: str, tipo: str, est: int) -> dict:
    """Cria o item no inventário (bônus cresce com o perigo do mapa e as estrelas) e devolve a visão dele."""
    bonus = max(1, round(2 * mp["perigo"] * ESTRELA_BONUS.get(est, 1.0)))
    it = InventoryItem(player_id=p.id, item_name=nome + (f" (superior {'★' * est})" if est else ""),
                       item_type=ItemType(tipo), status_bonus={attr: bonus})
    db.add(it)
    db.flush()
    return item_view(it, est)


def evento_passivo(db: Session, p: Player, mp: dict, ev: str) -> dict:
    """Eventos sem luta. Curandeiro: recupera metade da vida e do chakra e cura status. Tesouro: ryos e às vezes um item."""
    if ev == "curandeiro":
        p.vida_atual = min(p.vida_max, p.vida_atual + p.vida_max // 2)
        p.chakra_atual = min(p.chakra_max, p.chakra_atual + p.chakra_max // 2)
        curou = bool(p.status_fora)
        p.status_fora = []
        txt = "Um curandeiro itinerante cuidou dos seus ferimentos: +50% de vida e de chakra" + (" e curou seus status." if curou else ".")
        return {"tipo": "curandeiro", "emoji": "🌿", "titulo": "Curandeiro itinerante", "texto": txt, "ryos": 0, "drops": []}
    ryos = random.randint(12, 25) * mp["perigo"]
    p.ryos += ryos
    drops = []
    if random.random() < 0.4 and bolsa_livre(p):
        nome, attr, *t = random.choice(mp["itens"])
        drops.append(criar_item(db, p, mp, nome, attr, t[0] if t else mp["tipo"], sortear_estrelas(min(MORAL_MAX, p.moral + 10))))
    txt = f"Você encontrou um tesouro escondido: {ryos} ryos" + (f" e {drops[0]['nome']}." if drops else ".")
    return {"tipo": "tesouro", "emoji": "💰", "titulo": "Tesouro escondido", "texto": txt, "ryos": ryos, "drops": drops}


def item_view(it: InventoryItem, estrelas: int = 0) -> dict:
    return {"id": it.id, "nome": it.item_name, "tipo": it.item_type.value, "bonus": it.status_bonus or {}, "valor": valor_venda(it), "estrelas": estrelas}


def escolher_acao(p: Player):
    """IA do combate rápido: cura com vida baixa, antídoto se houver status, senão a técnica mais forte que o chakra paga."""
    L, pocoes = p.luta, p.pocoes or {}
    if (L.get("st") or L.get("me_atordoado")) and pocoes.get("antidoto", 0) > 0:
        return "pocao", "antidoto"
    if p.vida_atual < 0.35 * p.vida_max:
        for k in ("vida_g", "vida_m", "vida_p"):
            if pocoes.get(k, 0) > 0:
                return "pocao", k
    pagas = [a for a in _acoes(p, L) if a["custo"] and p.chakra_atual + L.get("reserva", 0) >= a["custo"]]
    if pagas:
        a = max(pagas, key=lambda a: (a["mod"], a["custo"]))
        return a["acao"], a.get("item")
    return "fisico", None


def _linha(quem: str, acao: str, dmg: int, crit: bool, nome: str = None) -> str:
    nome = nome or NOME_ACAO[acao]
    if not dmg:
        return f"{quem} tentou {nome}, mas errou."
    return f"{quem} usou {nome}: {dmg} de dano" + (" (crítico!)" if crit else "") + "."


def turno_inimigo(L: dict, me: dict, p: Player) -> None:
    if L["atordoado"]:
        L["atordoado"] = False
        L["log"].append(f"{L['nome']} está atordoado e perde o turno.")
        return
    f, acao = L["stats"], "fisico"
    if L["chakra"] >= custo_tecnica(f) and random.random() < 0.4:
        acao = "ninjutsu" if f["ninjutsu"] >= f["genjutsu"] else "genjutsu"
        L["chakra"] -= custo_tecnica(f)
    mod = mod_elem(L.get("elemento"), L.get("meu_elem")) if acao == "ninjutsu" else 1.0
    dmg, crit, stun = golpe(f, me, acao, mod=mod)
    nome = BASICO["ninjutsu"][L["elemento"]] if acao == "ninjutsu" and L.get("elemento") in ELEMENTOS else None
    if dmg and L["defendendo"]:
        dmg = max(1, dmg // 2)
    L["defendendo"] = False
    absorvido = min(L.get("escudo", 0), dmg)  # o escudo de vigor absorve o dano primeiro
    L["escudo"] = L.get("escudo", 0) - absorvido
    p.vida_atual = max(0, p.vida_atual - (dmg - absorvido))
    L["log"].append(_linha(L["nome"], acao, dmg, crit, nome) + (" Você ficou atordoado!" if stun and dmg else ""))
    if stun and dmg:
        L["me_atordoado"] = True
    if dmg:  # chance de causar um status negativo, conforme o tipo de golpe
        ef = (ELEM_STATUS.get(L.get("elemento")) if acao == "ninjutsu" and random.random() < 0.28 else
              "sangrando" if acao == "fisico" and random.random() < 0.10 else "cego" if acao == "genjutsu" and random.random() < 0.20 else None)
        if ef and ef not in L.setdefault("st", {}):
            aplicar_status(L, ef)


def _me(p: Player, L: dict) -> dict:
    """Atributos do jogador na luta: equipamentos, clã, invocação e o bônus do pet, se ele ativou."""
    s = effective_stats(p)
    if L.get("pet"):
        s[L["pet"]["attr"]] += L["pet"]["n"]
    s["_classe"] = atributos5.classe_efetiva(p)  # Bloco 3/7: o atributo principal do dano vem da classe
    return s


def iniciar_luta(p: Player, tipo: str, nome: str, stats: dict, hp: int, chakra: int, boss: bool = False, **ctx) -> None:
    L = {"tipo": tipo, "nome": nome, "boss": boss, "stats": stats, "hp": hp, "hp_max": hp, "chakra": chakra,
         "turno": 0, "log": [f"{nome} aparece diante de você."], "defendendo": False, "atordoado": False,
         "me_atordoado": False, "pet": None, "pet_id": None, "meu_elem": p.elemento,
         "elemento": random.choice(list(ELEMENTOS)), "st": {s: 2 for s in (p.status_fora or []) if s in STATUS_TURNOS}, **ctx}
    if L["st"]:
        L["log"].append("Você começa a luta " + ", ".join(L["st"]) + ".")
    p.status_fora = []
    pet = object_session(p).scalar(select(Pet).where(Pet.player_id == p.id, Pet.ativo.is_(True)))
    if pet:  # a chance de o bônus do pet ativar cresce com a afinidade
        L["pet_id"] = pet.id
        attr, n = bonus_pet(pet)
        if random.random() < chance_pet(pet.afinidade):
            L["pet"] = {"nome": pet.especie, "attr": attr, "n": n}
            L["log"].append(f"Seu {pet.especie} ({pet.natureza}) ajuda na luta: +{n} de {attr}!")
    me = _me(p, L)
    # Vigor e chakra acima do valor treinado (itens, clã, pet) viram escudo de vida e reserva de chakra na luta
    L["escudo"] = max(0, 5 * (me["vigor"] - p.vigor))
    L["reserva"] = max(0, 4 * (me["chakra_attr"] - p.chakra_attr))
    if L.get("emboscada") or stats["agilidade"] > me["agilidade"] or (stats["agilidade"] == me["agilidade"] and random.random() < 0.5):  # iniciativa: o mais ágil começa; empate no sorteio
        L["log"].append(f"Emboscada! {nome} ataca primeiro." if L.get("emboscada") else f"{nome} é mais rápido e ataca primeiro.")
        turno_inimigo(L, me, p)
        p.vida_atual = max(1, p.vida_atual)
    p.luta = L


def _acoes(p: Player, L: dict) -> list:
    """Ações de ataque com nome: básicas (por elemento) e os jutsus aprendidos, já com a vantagem contra o inimigo."""
    c, foe = custo_tecnica(_me(p, L)), L.get("elemento")
    acoes = [{"acao": "fisico", "nome": BASICO["fisico"], "custo": 0, "mod": 1.0, "elem": None},
             {"acao": "ninjutsu", "nome": BASICO["ninjutsu"][p.elemento], "custo": c, "mod": mod_elem(p.elemento, foe), "elem": p.elemento},
             {"acao": "genjutsu", "nome": BASICO["genjutsu"], "custo": c, "mod": 1.0, "elem": None}]
    for nv in MARCOS:
        j = JUTSUS.get((p.jutsus or {}).get(str(nv), ""))
        if j:
            acoes.append({"acao": "jutsu", "item": j["id"], "nome": j["nome"], "custo": max(1, round(c * j["custo"])),
                          "mod": mod_elem(j["elem"], foe), "elem": j["elem"], "cat": j["cat"]})
    return acoes


def luta_view(p: Player):
    L = p.luta
    if not L:
        return None
    return {"inimigo": L["nome"], "chefe": L["boss"], "tipo": L["tipo"], "hp": max(0, L["hp"]), "hp_max": L["hp_max"],
            "turno": L["turno"], "log": L["log"][-8:], "custo": custo_tecnica(_me(p, L)), "tecnica": p.tecnica, "pet": L.get("pet"),
            "escudo": L.get("escudo", 0), "reserva": L.get("reserva", 0), "elemento": L.get("elemento"), "acoes": _acoes(p, L),
            "pocoes": [{"id": k, "nome": POCOES[k][0], "qtd": q} for k, q in (p.pocoes or {}).items()
                       if q > 0 and k in POCOES and POCOES[k][1] in ("vida", "chakra", "cura")],
            "nivel": L.get("nivel"), "dif": L.get("dif"), "mapa": L.get("mapa"), "emboscada": bool(L.get("emboscada")),
            "andar": L.get("andar"), "total": L.get("total"), "log_full": L["log"][-40:], "moral": p.moral,
            "status": list(L.get("st", {})) + (["atordoado"] if L.get("me_atordoado") else []),
            "rapido": L["tipo"] == "pve" and (p.abates or {}).get(L["nome"], 0) >= ABATES_RAPIDO, "treino": L.get("tr")}


def fim_luta(db: Session, p: Player, L: dict, won: bool) -> dict:
    """Aplica recompensas (PvE: XP, ryos, drops, missão, fama; duelo: XP e fama) e encerra a luta."""
    p.luta = None
    p.vida_atual = max(1, p.vida_atual)
    dif = L.get("dif") or ("chefe" if L["boss"] else "normal")
    p.status_fora = list(L.get("st", {})) if won else []  # na derrota o hospital leva embora os status
    if won:
        p.moral = min(MORAL_MAX, p.moral + 1)
    else:
        p.moral = 0  # morrer zera a moral
    res = {"vitoria": won, "chefe": L["boss"], "inimigo": L["nome"], "log": L["log"][-8:], "exp": 5, "ryos": 0,
           "drops": [], "extras": [], "novo_pet": None, "niveis_ganhos": 0, "xp_reduzido": False, "missao": None,
           "dif": dif, "nivel": L.get("nivel"), "mapa": L.get("mapa"), "tipo": L["tipo"], "on_mission": L.get("on_mission", False), "moral": p.moral}
    if L["tipo"] == "pvp":
        if won:
            res["exp"] = 4
            p.fama += 3
    elif L["tipo"] == "clan":  # duelo final da provação: vencer significa ingressar no clã
        if won:
            clan = db.get(Clan, L["clan_id"])
            res["exp"] = 25
            p.fama += 10
            p.clan_id, p.prov_clan_id, p.prov_etapa, p.prov_prog, p.clan_pontos = clan.id, None, 0, 0, 0
            res["clan"] = f"Você foi aceito no {clan.name}!"
    elif L["tipo"] == "gchefe":  # chefe cooperativo da guilda: o dano causado entra no HP compartilhado
        fim_gchefe(db, p, L, won, res)
    elif L["tipo"] == "arena":  # duelo ranqueado: troféus, moedas e registro
        fim_arena(db, p, L, won, res)
    elif L["tipo"] == "masmorra":  # andar de uma masmorra: o resto da corrida segue em p.masmorra
        fim_masmorra(db, p, L, won, res)
    elif L["tipo"] == "torre":  # andar da Torre: a primeira vitória em cada andar rende a recompensa
        fim_torre(db, p, L, won, res)
    else:
        mp = MAPS_BY_ID[L["mapa"]]
        mult = DIFICULDADES[dif][2] * (1.3 if L.get("emboscada") else 1)
        over = p.level > mp["max_level"]
        res["xp_reduzido"] = over
        if won:
            res["exp"] = max(1, int(6 * mp["perigo"] * mult * (0.25 if over else 1)))
            res["ryos"] = 8 * mp["perigo"] * mult
            bg = bonus_guilda(p)  # perk de nível da guilda: +1% de XP e ryos por nível (até +15%)
            if bg:
                res["exp"], res["ryos"] = int(res["exp"] * (1 + bg / 100)), int(res["ryos"] * (1 + bg / 100))
                res["extras"].append(f"bônus da guilda +{bg}%")
            mr, mx = multiplicador_ryos(p), multiplicador_xp(p)  # bônus do VIP
            if mr > 1 or mx > 1:
                res["exp"], res["ryos"] = int(res["exp"] * mx), int(res["ryos"] * mr)
                res["extras"].append(f"bônus VIP: ryos +{round((mr - 1) * 100)}%" + (f", XP +{round((mx - 1) * 100)}%" if mx > 1 else ""))
            p.ryos += res["ryos"]
            p.fama += 5 if L["boss"] else 1
            clan_hooks(db, p, L)
            pet_afinidade(db, L)
            ab = dict(p.abates or {})
            ab[L["nome"]] = ab.get(L["nome"], 0) + 1
            p.abates = ab
            moral_antes = res["moral"] - 1
            for pid, chance, perigo_min in CONSUMIVEIS_DROP:  # consumíveis também caem, com os melhores nos mapas perigosos
                if mp["perigo"] >= perigo_min and random.random() < chance:
                    p.pocoes = {**(p.pocoes or {}), pid: (p.pocoes or {}).get(pid, 0) + 1}
                    res["extras"].append(POCOES[pid][0])
            if L["on_mission"]:  # coleta: 70% por vitória; caçada: 1 por vitória; procurado: só ao vencer o chefe
                mis = mission_for(p.journey + 1, vila_idx(p))
                gain = {"coleta": int(random.random() < 0.7), "caca": 1, "busca": int(L["boss"])}[mis["tipo"]]
                p.journey_prog = min(mis["qtd"], p.journey_prog + gain)
                res["missao"] = {"progresso": p.journey_prog, "qtd": mis["qtd"]}
    antes = len(p.elementos_extra or [])
    res["niveis_ganhos"] = apply_exp(p, res["exp"])
    if len(p.elementos_extra or []) > antes:
        res["elemento_novo"] = p.elementos_extra[-1]
    try:  # missões, conquistas, passe e bônus de evento
        from progresso_extra import registrar_luta
        registrar_luta(p, L, res, won)
    except Exception:
        pass
    return res


@router.get("/api/player/{telegram_id}/luta")
def luta_atual(telegram_id: int, db: Session = Depends(get_db)):
    return {"luta": luta_view(get_player(db, telegram_id))}


@router.post("/api/maps/explore")
def explore(payload: ExploreReq, db: Session = Depends(get_db)):
    """Explorar gasta 1 de energia e abre uma luta por turnos contra um inimigo do mapa."""
    player = get_player(db, payload.telegram_id, lock=True)
    mp = mapa_vila(payload.map_id, vila_idx(player)) if payload.map_id in MAPS_BY_ID else None
    if player.luta:
        raise HTTPException(409, "Você já está em uma luta.")
    if mp is None:
        raise HTTPException(404, "Mapa não encontrado.")
    if player.level < mp["min_level"]:
        raise HTTPException(403, f"Mapa bloqueado: exige nível {mp['min_level']}.")
    if player.energia < 1:
        raise HTTPException(400, "Sem energia. Ela recupera 1 a cada 3 minutos.")
    if player.vida_atual < 10:
        raise HTTPException(400, "Vida baixa demais. Descanse antes de explorar.")

    mis = mission_for(player.journey + 1, vila_idx(player))
    on_mission = mis["mapa"] == mp["id"] and player.level >= mis["req_nivel"] and player.journey_prog < mis["qtd"]
    # Bloco 4: Pity do Boss (Pontos de Ameaça por mapa) + curva de spawn 55/33/11/1
    ameaca = db.get(BossThreat, (player.id, mp["id"]))
    if ameaca is None:
        ameaca = BossThreat(player_id=player.id, mapa_id=mp["id"], pontos=0)
        db.add(ameaca)
    sp = bloco4.rolar_spawn(ameaca.pontos, mp["min_level"], mp["max_level"],
                            forcar_chefe=on_mission and mis["tipo"] == "busca" and random.random() < 0.5)
    ameaca.pontos = sp.pontos  # +1 sem chefe; 0 no instante em que o chefe aparece
    drops = rolar_drops_exploracao(db, player, mp)  # Bloco 4: dois rolos independentes por clique
    info = lambda: {"drops": drops, "ameaca": {"pontos": ameaca.pontos, "chance_chefe": bloco4.chance_chefe(ameaca.pontos)}}
    ev = None if sp.chefe else sortear_evento()
    if ev in ("curandeiro", "tesouro"):
        player.energia -= 1
        res = evento_passivo(db, player, mp, ev)
        db.commit()
        return {"luta": None, "evento": res, **info()}
    dif, nivel = ("chefe", sp.nivel) if sp.chefe else (dif_por_nivel(player, sp.nivel), sp.nivel)
    emboscada = ev == "emboscada" and dif in ("fácil", "normal", "difícil")
    if emboscada:  # os inimigos surgem em maior número: um degrau acima na dificuldade e o primeiro golpe é deles
        dif = {"fácil": "normal", "normal": "difícil", "difícil": "desafiante"}[dif]
    boss = dif in ("chefe", "impossível")
    comum = random.choice([mp["inimigo"]] * 2 + mp.get("extras", []))  # o inimigo da vila é o mais comum; os outros dão variedade
    nome = mp["chefe"] if boss else f"{comum} Alfa" if dif == "mini-chefe" else comum
    stats, hp, ck = stats_inimigo(player, nivel, dif)
    player.energia -= 1
    iniciar_luta(player, "pve", nome, stats, hp, ck, boss, mapa=mp["id"], on_mission=on_mission,
                 elemento=elem_mapa(vila_idx(player)), dif=dif, nivel=nivel, emboscada=emboscada)
    if drops and player.luta:  # os drops do clique aparecem no log da luta
        L = dict(player.luta)
        L["log"] = L["log"] + ["Você encontrou: " + ", ".join(drops) + "."]
        player.luta = L
    db.commit()
    return {**info(), "luta": luta_view(player), "evento": {"tipo": "emboscada", "emoji": "⚠️", "titulo": "Emboscada!",
                                                   "texto": "Você caiu numa emboscada: o inimigo é mais forte e ataca primeiro, mas a recompensa é 30% maior."} if emboscada else None}


@router.post("/api/pvp/desafiar")
def pvp_desafiar(payload: PvpReq, db: Session = Depends(get_db)):
    """Duelo contra outro jogador: ele luta com os atributos reais e a vida cheia, sem sofrer dano nem perder nada."""
    p = get_player(db, payload.telegram_id, lock=True)
    if p.luta:
        raise HTTPException(409, "Você já está em uma luta.")
    t = db.scalar(select(Player).where(Player.ninja_name == payload.alvo))
    if t is None:
        raise HTTPException(404, "Jogador não encontrado.")
    if t.id == p.id:
        raise HTTPException(400, "Você não pode duelar consigo mesmo.")
    if p.energia < 2:
        raise HTTPException(400, "Duelos custam 2 de energia.")
    if p.vida_atual < 10:
        raise HTTPException(400, "Vida baixa demais. Descanse antes de duelar.")
    p.energia -= 2
    iniciar_luta(p, "pvp", t.ninja_name, {**{k: int(v) for k, v in effective_stats(t).items()}, "_classe": atributos5.classe_efetiva(t)}, t.vida_max, t.chakra_max, elemento=t.elemento, alvo_id=t.id)
    db.commit()
    return {"luta": luta_view(p)}


@router.post("/api/luta/agir")
def luta_agir(payload: AgirReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    if not p.luta:
        raise HTTPException(409, "Você não está em luta.")
    if p.luta.get("tipo") == "boneco":  # treino no boneco: nada é ganho nem gasto (boneco_extra.py)
        from boneco_extra import agir_boneco
        return agir_boneco(db, p, payload)
    L, acao = copy.deepcopy(p.luta), payload.acao
    me = _me(p, L)
    L["turno"] += 1
    perdeu = efeitos_turno(p, L)
    if p.vida_atual <= 0:  # morreu por queimadura ou sangramento
        res = fim_luta(db, p, L, False)
        db.commit()
        return {"luta": None, "resultado": res}
    if L["me_atordoado"]:
        L["me_atordoado"] = False
        L["log"].append("Você está atordoado e perde o turno.")
    elif perdeu:
        pass  # o motivo (congelado ou paralisado) já foi ao log
    elif acao == "fugir" and L["tipo"] in ("arena", "gchefe"):
        raise HTTPException(400, "Não há fuga aqui: lute até o fim.")
    elif acao == "fugir":
        if random.random() < 0.3 + 0.5 * me["agilidade"] / (me["agilidade"] + L["stats"]["agilidade"]):
            p.luta = None
            p.status_fora = list(L.get("st", {}))
            if L["tipo"] == "masmorra":  # fugir no meio da masmorra encerra a corrida
                p.masmorra = None
            db.commit()
            return {"luta": None, "resultado": {"vitoria": False, "fuga": True, "chefe": L["boss"], "inimigo": L["nome"],
                    "log": L["log"][-4:] + ["Você fugiu."], "exp": 0, "ryos": 0, "drops": [], "extras": [], "novo_pet": None,
                    "niveis_ganhos": 0, "xp_reduzido": False, "missao": None, "dif": L.get("dif"), "nivel": L.get("nivel"),
                    "mapa": L.get("mapa"), "tipo": L["tipo"], "on_mission": L.get("on_mission", False), "moral": p.moral}}
        L["log"].append("Você não conseguiu fugir.")
    elif acao == "pocao":  # usar poção gasta o turno
        if not payload.item:
            raise HTTPException(400, "Escolha uma poção.")
        if payload.item == "antidoto":  # cura todos os status da luta
            if not (L.get("st") or L["me_atordoado"]):
                raise HTTPException(400, "Você não está com nenhum status negativo.")
            if (p.pocoes or {}).get("antidoto", 0) <= 0:
                raise HTTPException(400, "Você não tem Antídoto.")
            p.pocoes = {**p.pocoes, "antidoto": p.pocoes["antidoto"] - 1}
            L["st"], L["me_atordoado"] = {}, False
            L["log"].append("Você usa Antídoto: todos os status foram curados.")
        else:
            L["log"].append("Você usa " + usar_pocao(p, payload.item) + ".")
    elif acao == "defender":
        L["defendendo"] = True
        p.chakra_atual = min(p.chakra_max, p.chakra_atual + 2)
        L["log"].append("Você se defende e recupera o fôlego.")
    else:
        poder, crit_extra, base, custo, nome_golpe, el = 1.0, 0.0, acao, 0, BASICO["fisico"], None
        if acao == "jutsu":
            j = JUTSUS.get(payload.item or "")
            if j is None or payload.item not in (p.jutsus or {}).values():
                raise HTTPException(400, "Você ainda não aprendeu esse jutsu.")
            base = {"ninjutsu": "ninjutsu", "genjutsu": "genjutsu", "taijutsu": "fisico"}[j["cat"]]
            poder, el, nome_golpe = j["poder"], j["elem"], j["nome"]
            crit_extra = 0.15 if j["cat"] == "taijutsu" else 0.0
            custo = max(1, round(custo_tecnica(me) * j["custo"]))
        elif acao == "ninjutsu":
            custo, el, nome_golpe = custo_tecnica(me), p.elemento, BASICO["ninjutsu"][p.elemento]
        elif acao == "genjutsu":
            custo, nome_golpe = custo_tecnica(me), BASICO["genjutsu"]
        elif acao == "secreta":
            if not p.tecnica:
                raise HTTPException(400, "Você ainda não domina uma técnica secreta.")
            custo, nome_golpe = custo_tecnica(me) * 3, p.tecnica
        if custo:
            if p.chakra_atual + L.get("reserva", 0) < custo:
                raise HTTPException(400, f"Chakra insuficiente: {nome_golpe} custa {custo}.")
            da_reserva = min(L.get("reserva", 0), custo)  # a reserva de bônus é gasta primeiro
            L["reserva"] = L.get("reserva", 0) - da_reserva
            p.chakra_atual -= custo - da_reserva
        mod = mod_elem(el, L.get("elemento"))
        dmg, crit, stun = golpe(me, L["stats"], base, poder, mod, crit_extra)
        if dmg and "cego" in L.get("st", {}) and random.random() < 0.35:  # cegueira: o golpe erra o alvo
            dmg, crit, stun = 0, False, False
        nota = " Vantagem elemental!" if dmg and mod > 1 else " O elemento do inimigo resiste." if dmg and mod < 1 else ""
        L["log"].append(_linha("Você", base, dmg, crit, nome_golpe) + nota + (" O inimigo ficou atordoado!" if stun and dmg else ""))
        L["hp"] -= dmg
        if stun and dmg:
            L["atordoado"] = True
        if L["hp"] <= 0:
            res = fim_luta(db, p, L, True)
            db.commit()
            return {"luta": None, "resultado": res}
    turno_inimigo(L, me, p)
    if p.vida_atual <= 0:
        res = fim_luta(db, p, L, False)
        db.commit()
        return {"luta": None, "resultado": res}
    L["log"] = L["log"][-60:]
    p.luta = L
    db.commit()
    return {"luta": luta_view(p), "resultado": None}


@router.post("/api/luta/rapida")
def luta_rapida(payload: RestReq, db: Session = Depends(get_db)):
    """Combate rápido: depois de 30 vitórias contra o mesmo inimigo, resolve a luta atual sozinho e devolve o resultado."""
    p = get_player(db, payload.telegram_id)
    if not p.luta:
        raise HTTPException(409, "Você não está em luta.")
    if p.luta["tipo"] != "pve" or (p.abates or {}).get(p.luta["nome"], 0) < ABATES_RAPIDO:
        raise HTTPException(403, f"O combate rápido libera após {ABATES_RAPIDO} vitórias contra este inimigo.")
    for _ in range(80):
        p = get_player(db, payload.telegram_id)
        if not p.luta:
            break
        acao, item = escolher_acao(p)
        r = luta_agir(AgirReq(telegram_id=payload.telegram_id, acao=acao, item=item), db)
        if r.get("resultado"):
            return r
    raise HTTPException(500, "A luta não terminou. Continue manualmente.")


@router.post("/api/inventory/equip")
def equip(payload: EquipReq, db: Session = Depends(get_db)):
    """Equipa/desequipa um item. Um item equipado por tipo; anéis aceitam dois."""
    player = get_player(db, payload.telegram_id, lock=True)
    item = next((i for i in player.inventory if i.id == payload.item_id), None)
    if item is None:
        raise HTTPException(404, "Item não encontrado no seu inventário.")
    if payload.equipar:
        # Um item equipado por tipo; anéis aceitam dois (o equipado há mais tempo, de menor id, sai primeiro)
        limite = MAX_EQUIP.get(item.item_type.value, 1)
        iguais = sorted((o for o in player.inventory if o.item_type == item.item_type and o.is_equipped and o.id != item.id), key=lambda o: o.id)
        for other in iguais[:max(0, len(iguais) - (limite - 1))]:
            other.is_equipped = False
    item.is_equipped = payload.equipar
    db.commit()
    return {"ok": True}


@router.post("/api/summon/activate")
def activate_summon(payload: SummonReq, db: Session = Depends(get_db)):
    """Define qual invocação acompanha o jogador (apenas uma ativa)."""
    player = get_player(db, payload.telegram_id, lock=True)
    if not any(ps.summon_id == payload.summon_id for ps in player.player_summons):
        raise HTTPException(404, "Você não possui essa invocação.")
    for ps in player.player_summons:
        ps.is_active = ps.summon_id == payload.summon_id
    db.commit()
    return {"ok": True}


@router.post("/api/clan/join")
def join_clan(payload: ClanReq, db: Session = Depends(get_db)):
    """Inicia a provação de ingresso do clã: várias etapas, terminando em duelo contra o líder."""
    player = get_player(db, payload.telegram_id, lock=True)
    clan = db.get(Clan, payload.clan_id)
    if clan is None or clan.name not in CLAN_DATA:
        raise HTTPException(404, "Clã não encontrado.")
    if player.clan.name != NO_CLAN_NAME:
        raise HTTPException(409, "Você já pertence a um clã.")
    if player.prov_clan_id:
        raise HTTPException(409, "Você já está em uma provação. Abandone-a antes de começar outra.")
    min_level, min_rank = clan_req(clan)
    if player.level < min_level or not rank_ok(player, min_rank):
        raise HTTPException(403, f"Requisitos não atingidos: nível {min_level} e rank {min_rank}.")
    player.prov_clan_id, player.prov_etapa, player.prov_prog = clan.id, 0, 0
    prova_avancar(db, player)
    db.commit()
    return {"entrou": True, "mensagem": f"Provação do {clan.name} iniciada."}


@router.post("/api/rest")
def rest(payload: RestReq, db: Session = Depends(get_db)):
    """Casa de chá: restaura vida e chakra por uma taxa em ryos."""
    player = get_player(db, payload.telegram_id, lock=True)
    if player.masmorra:
        raise HTTPException(409, "Não dá para descansar durante a masmorra: use os itens da bolsa ou abandone-a.")
    if player.torre_sessao:
        raise HTTPException(409, "Não dá para descansar dentro da Torre: use os itens da bolsa ou saia da Torre.")
    if player.ryos < REST_COST:
        raise HTTPException(400, f"Ryos insuficientes: o descanso custa {REST_COST}.")
    player.ryos -= REST_COST
    player.vida_atual, player.chakra_atual = player.vida_max, player.chakra_max
    db.commit()
    return {"ok": True}


# ───────── Jornada: missões por tarefa (coleta, caçada, procurado), sem tempo ─────────
# seq da missão: (nome, nível mínimo, patente, fama mínima). A fama vem das missões da Jornada e das lutas.
EXAMES = {6: ("Exame Genin", 5, "Genin", 0), 16: ("Exame Chunin", 12, "Chunin", 0), 32: ("Exame Jonin", 20, "Jonin", 500),
          46: ("Exame Jonin de Elite", 28, "Jonin de Elite", 1000), 62: ("Exame Anbu", 38, "Anbu", 2500),
          84: ("Exame Anbu de Elite", 52, "Anbu de Elite", 6000)}
TIPO_NOME = {"coleta": "Coleta", "caca": "Caçada", "busca": "Procurado"}


def mission_for(seq: int, vi: int = 0) -> dict:
    """Missão nº `seq` (infinita). Tipos em rodízio: coleta, caçada e procurado do Livro Bingo. Exames promovem o rank.
    Números iguais em todas as vilas; mapas, recursos, procurados e arcos de história são os da vila `vi`."""
    v = VILAS[vi]
    exame = EXAMES.get(seq)
    req = exame[1] if exame else max(1, int(seq * 0.55))
    tipo = "caca" if exame else ("busca", "coleta", "caca")[seq % 3]
    mid = next((m["id"] for m in reversed(MAPS) if m["min_level"] <= req), 1)
    mp = mapa_vila(mid, vi)
    qtd = {"coleta": 4 + seq // 4, "caca": 5 + seq // 3, "busca": 1}[tipo]
    objetivo = {"coleta": f"Colete {qtd} {recurso_vila(vi, mid)} em {mp['nome']}",
                "caca": f"Derrote {qtd} inimigos em {mp['nome']}",
                "busca": f"Capture {mp['chefe']}, procurado em {mp['nome']}"}[tipo]
    arco, sinopse = v["arcos"][((seq - 1) // 6) % len(v["arcos"])]
    if exame:
        titulo, historia = exame[0], f"O conselho de {v['nome']} avaliou seus feitos e abriu o {exame[0].lower()}."
    else:
        titulo, historia = f"{arco}: {TIPO_NOME[tipo]}", sinopse
    return {"seq": seq, "titulo": titulo, "historia": historia, "tipo": tipo, "mapa": mid, "objetivo": objetivo,
            "qtd": qtd, "req_nivel": req, "exame": exame[2] if exame else None, "exame_fama": exame[3] if exame else 0, "rank": "DCBAS"[min(4, seq // 12)],
            "exp": 10 + 4 * seq, "ryos": 20 + 8 * seq, "fama": (10 + seq) * (3 if tipo == "busca" else 1)}


class JornadaDone(BaseModel):
    telegram_id: int = Field(gt=0)
    # Honra sobe a reputação; sombra rende +30% de ryos, mas a derruba
    escolha: Literal["honra", "sombra"] = "honra"


@router.get("/api/player/{telegram_id}/jornada")
def jornada(telegram_id: int, db: Session = Depends(get_db)):
    """Missão atual e progresso. Missões já concluídas não são listadas."""
    p = get_player(db, telegram_id)
    m = mission_for(p.journey + 1, vila_idx(p))
    return {**m, "progresso": p.journey_prog, "bloqueada": p.level < m["req_nivel"], "pronta": p.journey_prog >= m["qtd"]}


@router.post("/api/jornada/entregar")
def jornada_entregar(payload: JornadaDone, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    m = mission_for(p.journey + 1, vila_idx(p))
    if p.journey_prog < m["qtd"]:
        raise HTTPException(409, "O objetivo ainda não foi cumprido.")
    if m["exame"] and posto(p) < PATENTES.index(m["exame"]) and p.fama + m["fama"] < m["exame_fama"]:
        raise HTTPException(403, f"O {m['titulo']} exige {_n(m['exame_fama'])} de fama (você tem {_n(p.fama)}). Vença lutas e missões para ganhar fama.")
    sombra = payload.escolha == "sombra"
    ryos = int(m["ryos"] * (1.3 if sombra else 1))
    p.ryos += ryos
    p.fama += m["fama"]
    p.reputation = max(-100, min(100, p.reputation + (-8 if sombra else 5)))
    p.journey += 1
    p.journey_prog = 0
    if sombra:  # o caminho da sombra rende fama Nukenin
        try:
            from blocos_extra import nk_ganhar
            nk_ganhar(p, 40)
        except Exception:
            pass
    promovido = None
    if m["exame"] and posto(p) < PATENTES.index(m["exame"]):
        promover(p, m["exame"])
        promovido = m["exame"]
    levels = apply_exp(p, m["exp"])
    prog(p, "jornada")
    db.commit()
    return {"exp": m["exp"], "ryos": ryos, "fama": m["fama"], "niveis_ganhos": levels, "promovido": promovido}


# ───────── Guildas: criar, entrar, doar, sair ─────────
GUILD_COST = 1000


class GuildCreate(BaseModel):
    telegram_id: int = Field(gt=0)
    nome: str = Field(min_length=3, max_length=32, pattern=r"^[\w ]+$")


class GuildJoin(BaseModel):
    telegram_id: int = Field(gt=0)
    guild_id: int = Field(gt=0)


class DonateReq(BaseModel):
    telegram_id: int = Field(gt=0)
    valor: int = Field(ge=10, le=1_000_000)


@router.get("/api/player/{telegram_id}/guilda")
def guilda_info(telegram_id: int, db: Session = Depends(get_db)):
    """Minha guilda (com membros e cofre) e a lista das demais, para entrar ou atacar."""
    p = get_player(db, telegram_id)
    guilds = db.scalars(select(Guild).order_by(Guild.vitorias_guerra.desc(), Guild.level.desc()).limit(30)).all()
    counts = dict(db.execute(select(Player.guilda_id, func.count()).where(Player.guilda_id.is_not(None))
                             .group_by(Player.guilda_id)).all())
    others = [{"id": g.id, "nome": g.name, "nivel": g.level, "membros": counts.get(g.id, 0),
               "vitorias": g.vitorias_guerra} for g in guilds if g.id != p.guilda_id]
    minha = None
    if p.guild:
        g = p.guild
        membros = db.scalars(select(Player).where(Player.guilda_id == g.id)
                             .order_by(Player.level.desc()).limit(50)).all()
        minha = {"id": g.id, "nome": g.name, "nivel": g.level, "vitorias": g.vitorias_guerra,
                 "doado": g.recursos_doados, "proximo_nivel": 500 * g.level ** 2, "sou_lider": g.leader_id == p.id,
                 "total_membros": counts.get(g.id, 0),
                 "membros": [{"nome": m.ninja_name, "nivel": m.level, "rank": rank_titulo(m), "avatar": m.avatar,
                              "lider": m.id == g.leader_id, "cargo": cargo_guilda(g, m.id),
                              "dano": ((g.dados or {}).get("chefe") or {}).get("dano", {}).get(str(m.id), 0)} for m in membros],
                 "cargo": cargo_guilda(g, p.id), "mural": (g.dados or {}).get("mural", ""), "limite": guild_limite(g),
                 "bonus": bonus_guilda(p), "chefe": chefe_view(g, p), "pendente": _pendente(g, p)}
    return {"minha": minha, "guildas": others}


@router.post("/api/guild/create")
def guild_create(payload: GuildCreate, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    nome = payload.nome.strip()
    if p.guilda_id:
        raise HTTPException(409, "Você já está em uma guilda.")
    if p.ryos < GUILD_COST:
        raise HTTPException(400, f"Criar uma guilda custa {GUILD_COST} ryos.")
    if db.scalar(select(Guild.id).where(Guild.name == nome)):
        raise HTTPException(409, "Já existe uma guilda com esse nome.")
    g = Guild(name=nome, leader_id=p.id)
    db.add(g)
    db.flush()
    p.guilda_id = g.id
    p.ryos -= GUILD_COST
    db.commit()
    return {"ok": True}


@router.post("/api/guild/join")
def guild_join(payload: GuildJoin, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    if p.guilda_id:
        raise HTTPException(409, "Você já está em uma guilda.")
    gj = db.get(Guild, payload.guild_id)
    if gj is None:
        raise HTTPException(404, "Guilda não encontrada.")
    if (gj.dados or {}).get("modo") == "candidatura":
        raise HTTPException(409, "Esta guilda só aceita candidaturas: envie a sua e espere o líder aprovar.")
    if db.scalar(select(func.count()).select_from(Player).where(Player.guilda_id == gj.id)) >= guild_limite(gj):
        raise HTTPException(409, f"A guilda está cheia (limite de {guild_limite(gj)} membros no nível {gj.level}).")
    p.guilda_id = payload.guild_id
    db.commit()
    return {"ok": True}


@router.post("/api/guild/leave")
def guild_leave(payload: RestReq, db: Session = Depends(get_db)):
    """Sai da guilda. Se for o líder, passa o comando ao membro de maior nível (ou desfaz a guilda vazia)."""
    p = get_player(db, payload.telegram_id, lock=True)
    if not p.guilda_id:
        raise HTTPException(409, "Você não está em uma guilda.")
    g = p.guild
    if g.leader_id == p.id:
        vices = (g.dados or {}).get("vices", [])
        heir = db.scalar(select(Player).where(Player.guilda_id == g.id, Player.id != p.id)
                         .order_by(Player.id.in_(vices).desc(), Player.level.desc()).limit(1))
        if heir:
            g.leader_id = heir.id
            _dados_set(g, vices=[v for v in vices if v != heir.id])
    else:
        _dados_set(g, vices=[v for v in (g.dados or {}).get("vices", []) if v != p.id])
    p.guilda_id = None
    db.flush()
    if g.leader_id == p.id:  # ninguém assumiu: guilda vazia
        db.delete(g)
    db.commit()
    return {"ok": True}


@router.post("/api/guild/donate")
def guild_donate(payload: DonateReq, db: Session = Depends(get_db)):
    """Doa ryos ao cofre. A guilda sobe de nível sozinha conforme o total doado."""
    p = get_player(db, payload.telegram_id, lock=True)
    if not p.guilda_id:
        raise HTTPException(409, "Você não está em uma guilda.")
    if p.ryos < payload.valor:
        raise HTTPException(400, "Ryos insuficientes.")
    g = p.guild
    p.ryos -= payload.valor
    g.recursos_doados += payload.valor
    g.level = max(g.level, 1 + int((g.recursos_doados / 500) ** 0.5))
    _dados_set(g, cofre=(g.dados or {}).get("cofre", 0) + payload.valor)  # o cofre é o saldo gastável da tesouraria
    prog(p, "doacao")
    db.commit()
    return {"nivel": g.level}


# ───────── Avatar ─────────
class AvatarReq(BaseModel):
    telegram_id: int = Field(gt=0)
    avatar: str = Field(max_length=8)


@router.post("/api/avatar")
def set_avatar(payload: AvatarReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    if payload.avatar not in AVATARS:
        raise HTTPException(400, "Avatar inválido.")
    p.avatar = payload.avatar
    db.commit()
    return {"ok": True}


# ───────── Kage: cargo disputado por fama ─────────
KAGE_DAYS = 7
KAGE_MIN_FAMA = 20000  # fama mínima para disputar o cargo de Kage
SANNIN_VAGAS = 3  # cadeiras de Sannin no mundo inteiro
SANNIN_MIN_FAMA = 10000  # fama mínima para disputar uma cadeira de Sannin


def kage_tick(db: Session, v: Village) -> None:
    """
    Resolve a disputa do cargo. O desafiante precisa ser da vila e manter fama MAIOR que a do Kage atual
    por 7 dias seguidos; se a fama dele cair ao nível do Kage, a disputa zera. Cumprido o prazo, assume o cargo.
    """
    leader = db.get(Player, v.leader_kage_id) if v.leader_kage_id else None
    atual = leader.fama if leader else v.npc_fama
    ch = db.get(Player, v.challenger_id) if v.challenger_id else None
    if ch and (ch.village_id != v.id or ch.fama <= atual or ch.fama < KAGE_MIN_FAMA or ch.rank != NinjaRank.SANNIN):
        v.challenger_id = v.challenger_since = None
        ch = None
    if ch and datetime.now(timezone.utc) - as_utc(v.challenger_since) >= timedelta(days=KAGE_DAYS):
        if leader:
            leader.rank, leader.elite = NinjaRank.ANBU, True
        ch.rank, ch.elite = NinjaRank.KAGE, False
        v.leader_kage_id = ch.id
        v.challenger_id = v.challenger_since = None


def sannin_tick(db: Session) -> None:
    """Cadeiras de Sannin (3 no mundo). Vaga livre: o Anbu mais famoso, com 10.000+ de fama, assume na hora.
    Cadeiras cheias: o Anbu que superar a fama do Sannin mais fraco por 7 dias seguidos toma o lugar dele."""
    now = datetime.now(timezone.utc)
    sannins = list(db.scalars(select(Player).where(Player.rank == NinjaRank.SANNIN)))
    cands = list(db.scalars(select(Player).where(Player.rank == NinjaRank.ANBU, Player.elite.is_(True), Player.fama >= SANNIN_MIN_FAMA)
                            .order_by(Player.fama.desc())))
    while cands and len(sannins) < SANNIN_VAGAS:
        c = cands.pop(0)
        c.rank, c.elite, c.sannin_desde = NinjaRank.SANNIN, False, None
        sannins.append(c)
    fraco = min(sannins, key=lambda s: s.fama) if len(sannins) >= SANNIN_VAGAS else None
    for i, c in enumerate(cands):
        if i == 0 and fraco and c.fama > fraco.fama:
            if c.sannin_desde is None:
                c.sannin_desde = now
            elif now - as_utc(c.sannin_desde) >= timedelta(days=KAGE_DAYS):
                fraco.rank, fraco.elite, c.rank, c.elite, c.sannin_desde = NinjaRank.ANBU, True, NinjaRank.SANNIN, False, None
        else:
            c.sannin_desde = None
    db.commit()


def _sannin_view(db: Session, p: Player) -> dict:
    vilas = {v.id: v.name for v in db.scalars(select(Village))}
    cadeiras = db.scalars(select(Player).where(Player.rank == NinjaRank.SANNIN).order_by(Player.fama.desc())).all()
    dias = None
    if p.sannin_desde:
        dias = max(1, KAGE_DAYS - (datetime.now(timezone.utc) - as_utc(p.sannin_desde)).days)
    return {"cadeiras": [{"nome": s.ninja_name, "fama": s.fama, "vila": vilas.get(s.village_id, ""), "eu": s.id == p.id}
                         for s in cadeiras],
            "vagas": max(0, SANNIN_VAGAS - len(cadeiras)), "minimo": SANNIN_MIN_FAMA, "minimo_kage": KAGE_MIN_FAMA,
            "voce": {"rank": p.rank.value, "elite": bool(p.elite), "titulo": rank_titulo(p), "dias_restantes": dias}}


def _kage_view(db: Session, p: Player) -> dict:
    v = p.village
    leader = db.get(Player, v.leader_kage_id) if v.leader_kage_id else None
    atual = leader.fama if leader else v.npc_fama
    ch = db.get(Player, v.challenger_id) if v.challenger_id else None
    days = None
    if ch:
        days = max(1, KAGE_DAYS - (datetime.now(timezone.utc) - as_utc(v.challenger_since)).days)
    sou = leader is not None and leader.id == p.id
    pode = (not sou and p.rank == NinjaRank.SANNIN and p.fama >= KAGE_MIN_FAMA and p.fama > atual
            and (ch is None or ch.id == p.id or p.fama > ch.fama))
    return {"kage": {"nome": leader.ninja_name if leader else v.npc_kage, "fama": atual, "npc": leader is None},
            "desafiante": {"nome": ch.ninja_name, "fama": ch.fama, "dias_restantes": days} if ch else None,
            "voce_e_kage": sou, "pode_desafiar": pode, "vila": v.name}


@router.get("/api/player/{telegram_id}/kage")
def kage_info(telegram_id: int, db: Session = Depends(get_db)):
    p = get_player(db, telegram_id)
    sannin_tick(db)
    kage_tick(db, p.village)
    db.commit()
    return {**_kage_view(db, p), "sannin": _sannin_view(db, p)}


@router.post("/api/kage/desafiar")
def kage_desafiar(payload: RestReq, db: Session = Depends(get_db)):
    """Abre (ou assume) a disputa pelo cargo de Kage da sua vila. Só Sannin com 20.000+ de fama e mais fama que o Kage."""
    p = get_player(db, payload.telegram_id, lock=True)
    v = p.village
    sannin_tick(db)
    kage_tick(db, v)
    if not _kage_view(db, p)["pode_desafiar"]:
        raise HTTPException(403, "Só na sua vila de origem: exige ser Sannin, ter 20.000 de fama e mais fama que o Kage (e que o desafiante atual).")
    if v.challenger_id != p.id:
        v.challenger_id, v.challenger_since = p.id, datetime.now(timezone.utc)
    db.commit()
    return {"ok": True}


# ───────── Ranking ─────────
@router.get("/api/ranking")
def ranking(db: Session = Depends(get_db)):
    """Top 20 por fama e por nível, e o Kage de cada vila."""
    def top(order):
        rows = db.execute(select(Player, Village.name).join(Village, Village.id == Player.village_id)
                          .order_by(*order).limit(20)).all()
        return [{"nome": p.ninja_name, "avatar": p.avatar, "vila": v, "rank": rank_titulo(p),
                 "nivel": p.level, "fama": p.fama} for p, v in rows]
    kages = []
    for v in db.scalars(select(Village).order_by(Village.id)):
        leader = db.get(Player, v.leader_kage_id) if v.leader_kage_id else None
        kages.append({"vila": v.name, "nome": leader.ninja_name if leader else v.npc_kage,
                      "fama": leader.fama if leader else v.npc_fama, "jogador": leader is not None})
    grads = []
    for nome in reversed(PATENTES):  # Kage -> Estudante
        cond = [Player.rank == NinjaRank(nome.replace(" de Elite", ""))]
        if nome.split()[0] in ("Jonin", "Anbu"):
            cond.append(Player.elite.is_(nome.endswith(" de Elite")))
        rows = db.execute(select(Player, Village.name).join(Village, Village.id == Player.village_id)
                          .where(*cond).order_by(Player.fama.desc(), Player.level.desc()).limit(5)).all()
        total = db.scalar(select(func.count()).select_from(Player).where(*cond))
        grads.append({"rank": nome, "total": total, "top": [
            {"nome": p.ninja_name, "avatar": p.avatar, "vila": v, "rank": rank_titulo(p), "nivel": p.level, "fama": p.fama}
            for p, v in rows]})
    return {"fama": top([Player.fama.desc()]), "nivel": top([Player.level.desc(), Player.exp.desc()]),
            "kages": kages, "graduacoes": grads}


# ───────── Clãs: provação, pontos, liderança e os 3 caminhos do líder ─────────
LIDER_MIN = 5000  # pontos de clã para liderar (e para desafiar o líder)
CAMINHO_CHEFES = 8  # chefes de mapa a derrotar no caminho do líder
CAMINHO_CUSTO = {"reliquia": 6000, "poder": 4000, "segredo": 3000}  # tributo em ryos
TITULOS_CLAN = [(0, "Aprendiz"), (300, "Membro"), (1000, "Elite"), (2500, "Veterano"), (5000, "Herdeiro")]


class CaminhoReq(BaseModel):
    telegram_id: int = Field(gt=0)
    caminho: Literal["reliquia", "poder", "segredo"]


def prova_avancar(db: Session, p: Player) -> None:
    """Avança as etapas que se cumprem sozinhas (lutas, chefe, fama). Tributo e duelo pedem ação do jogador."""
    d = CLAN_DATA[db.get(Clan, p.prov_clan_id).name]
    while p.prov_etapa < len(d["etapas"]):
        tok = d["etapas"][p.prov_etapa]
        if tok[0] == "k":
            ok = p.prov_prog >= int(tok[1:].split("x")[1])
        elif tok[0] == "b":
            ok = p.prov_prog >= 1
        elif tok[0] == "f":
            ok = p.fama >= int(tok[1:])
        else:
            break
        if not ok:
            break
        p.prov_etapa += 1
        p.prov_prog = 0


def clan_hooks(db: Session, p: Player, L: dict) -> None:
    """Vitória em mapa: pontos de clã, progresso da provação de ingresso e do caminho do líder."""
    if p.clan.name != NO_CLAN_NAME:
        p.clan_pontos += 15 if L["boss"] else 3
        if p.caminho and not p.caminho_ok and L["boss"] and p.clan.lider_id == p.id:
            p.caminho_prog = min(CAMINHO_CHEFES, p.caminho_prog + 1)
    if p.prov_clan_id:
        tok = CLAN_DATA[db.get(Clan, p.prov_clan_id).name]["etapas"][p.prov_etapa]
        if tok[0] == "k" and L["mapa"] == int(tok[1:].split("x")[0]):
            p.prov_prog += 1
        elif tok[0] == "b" and L["boss"] and L["mapa"] == int(tok[1:]):
            p.prov_prog = 1
        prova_avancar(db, p)


def clan_tick(db: Session, clan: Clan) -> None:
    """Liderança: o primeiro membro com 5.000 pontos assume o lugar do líder NPC. Contra um líder jogador, é
    preciso ter mais pontos que ele por 7 dias seguidos."""
    lider = db.get(Player, clan.lider_id) if clan.lider_id else None
    if lider and lider.clan_id != clan.id:
        lider, clan.lider_id = None, None
    cand = db.scalar(select(Player).where(Player.clan_id == clan.id, Player.clan_pontos >= LIDER_MIN,
                                          Player.id != (lider.id if lider else -1))
                     .order_by(Player.clan_pontos.desc()).limit(1))
    if cand is None or (lider and cand.clan_pontos <= lider.clan_pontos):
        clan.desafiante_id = clan.desafio_desde = None
    elif lider is None:
        clan.lider_id, clan.desafiante_id, clan.desafio_desde = cand.id, None, None
    elif clan.desafiante_id != cand.id:
        clan.desafiante_id, clan.desafio_desde = cand.id, datetime.now(timezone.utc)
    elif datetime.now(timezone.utc) - as_utc(clan.desafio_desde) >= timedelta(days=KAGE_DAYS):
        clan.lider_id, clan.desafiante_id, clan.desafio_desde = cand.id, None, None
    db.commit()


def dar_atributo(p: Player, attr: str, n: int) -> None:
    setattr(p, attr, getattr(p, attr) + n)
    if attr == "vigor":
        p.vida_max += 5 * n
        p.vida_atual += 5 * n
    elif attr in ("chakra_attr", "inteligencia"):
        p.chakra_max += 4 * n
        p.chakra_atual += 4 * n


def etapa_txt(tok: str, d: dict, vi: int) -> str:
    if tok[0] == "k":
        m, n = tok[1:].split("x")
        return f"Vencer {n} lutas em {mapa_vila(int(m), vi)['nome']}"
    if tok[0] == "b":
        mp = mapa_vila(int(tok[1:]), vi)
        return f"Derrotar o chefe de {mp['nome']} ({mp['chefe']})"
    if tok[0] == "f":
        return f"Alcançar {tok[1:]} de fama"
    if tok[0] == "t":
        return f"Pagar {tok[1:]} ryos de tributo ao clã"
    return f"Duelo contra o líder, {d['lider']}"


@router.get("/api/player/{telegram_id}/clan")
def clan_info(telegram_id: int, db: Session = Depends(get_db)):
    p = get_player(db, telegram_id)
    villages = list(db.scalars(select(Village).order_by(Village.id)))
    vilas, vi = [v.name for v in villages], [v.id for v in villages].index(p.village_id)
    meu = prova = None
    if p.clan.name != NO_CLAN_NAME:
        c = p.clan
        clan_tick(db, c)
        d = CLAN_DATA[c.name]
        lider = db.get(Player, c.lider_id) if c.lider_id else None
        des = db.get(Player, c.desafiante_id) if c.desafiante_id and c.desafio_desde else None
        sou = lider is not None and lider.id == p.id
        meu = {"nome": c.name, "vila": vilas[d["vila"]], "historia": d["historia"], "atributo": d["atr"], "bonus": d["bonus"],
               "pontos": p.clan_pontos, "titulo": "Líder" if sou else [t for m, t in TITULOS_CLAN if p.clan_pontos >= m][-1],
               "lider_min": LIDER_MIN,
               "lider": {"nome": lider.ninja_name if lider else d["lider"], "npc": lider is None, "eu": sou},
               "desafiante": {"nome": des.ninja_name, "pontos": des.clan_pontos,
                              "dias": max(1, KAGE_DAYS - (datetime.now(timezone.utc) - as_utc(c.desafio_desde)).days)} if des else None,
               "caminho": {"escolhido": p.caminho, "prog": p.caminho_prog, "meta": CAMINHO_CHEFES, "ok": p.caminho_ok,
                           "custos": CAMINHO_CUSTO, "reliquia": d["reliquia"], "reliquia_livre": not c.reliquia_dada,
                           "tecnica": d["tecnica"]} if sou else None}
    if p.prov_clan_id:
        pc = db.get(Clan, p.prov_clan_id)
        d = CLAN_DATA[pc.name]
        prova_avancar(db, p)
        db.commit()
        tok = d["etapas"][p.prov_etapa]
        prova = {"clan": pc.name, "prog": p.prov_prog, "tipo": tok[0], "acao": tok[0] in "td",
                 "meta": int(tok[1:].split("x")[1]) if tok[0] == "k" else 1 if tok[0] == "b" else 0,
                 "passos": [{"txt": etapa_txt(t, d, vila_idx(p)), "estado": "feito" if i < p.prov_etapa else "atual" if i == p.prov_etapa else "pendente"}
                            for i, t in enumerate(d["etapas"])]}
    catalogo = []
    for c in db.scalars(select(Clan).where(Clan.name != NO_CLAN_NAME)):
        d = CLAN_DATA.get(c.name)
        if d:
            catalogo.append({"id": c.id, "nome": c.name, "vila": vilas[d["vila"]], "minha_vila": d["vila"] == vi,
                             "historia": d["historia"], "atributo": d["atr"], "bonus": d["bonus"], "nivel": d["nivel"],
                             "rank": d["rank"], "etapas": len(d["etapas"]), "lider": d["lider"],
                             "pode": p.level >= d["nivel"] and rank_ok(p, d["rank"])})
    return {"meu": meu, "prova": prova, "catalogo": catalogo}


@router.post("/api/clan/prova/avancar")
def prova_acao(payload: RestReq, db: Session = Depends(get_db)):
    """Etapas que pedem ação: pagar o tributo ou enfrentar o líder do clã em duelo por turnos."""
    p = get_player(db, payload.telegram_id, lock=True)
    if not p.prov_clan_id:
        raise HTTPException(409, "Você não está em uma provação.")
    if p.luta:
        raise HTTPException(409, "Você já está em uma luta.")
    clan = db.get(Clan, p.prov_clan_id)
    d = CLAN_DATA[clan.name]
    prova_avancar(db, p)
    tok = d["etapas"][p.prov_etapa]
    duelo = False
    if tok[0] == "t":
        custo = int(tok[1:])
        if p.ryos < custo:
            raise HTTPException(400, f"Ryos insuficientes: o tributo é {custo}.")
        p.ryos -= custo
        p.prov_etapa += 1
        p.prov_prog = 0
        prova_avancar(db, p)
        msg = "Tributo pago ao clã."
    elif tok[0] == "d":
        if p.vida_atual < 10:
            raise HTTPException(400, "Vida baixa demais. Descanse antes do duelo.")
        power = 4 + d["nivel"]
        iniciar_luta(p, "clan", d["lider"], {a: power for a in ATTRS}, 100 + 12 * d["nivel"], 80 + 10 * d["nivel"], True, clan_id=clan.id, elemento=elem_mapa(d["vila"]))
        msg, duelo = f"{d['lider']} aceitou o duelo.", True
    else:
        raise HTTPException(400, "Esta etapa avança vencendo lutas nos mapas.")
    db.commit()
    return {"mensagem": msg, "duelo": duelo}


@router.post("/api/clan/sair")
def clan_sair(payload: RestReq, db: Session = Depends(get_db)):
    """Abandona a provação em andamento ou deixa o clã (perde os pontos de clã)."""
    p = get_player(db, payload.telegram_id, lock=True)
    if p.luta:
        raise HTTPException(409, "Termine a luta antes.")
    if p.clan.name != NO_CLAN_NAME:
        if p.clan.lider_id == p.id:
            p.clan.lider_id = None
        p.clan_id = db.scalar(select(Clan.id).where(Clan.name == NO_CLAN_NAME))
        p.clan_pontos, p.caminho, p.caminho_prog, p.caminho_ok = 0, None, 0, False
        msg = "Você deixou o clã."
    elif p.prov_clan_id:
        msg = "Provação abandonada."
    else:
        raise HTTPException(409, "Você não tem clã nem provação.")
    p.prov_clan_id, p.prov_etapa, p.prov_prog = None, 0, 0
    db.commit()
    return {"mensagem": msg}


@router.post("/api/clan/caminho")
def caminho_escolher(payload: CaminhoReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    if p.clan.name == NO_CLAN_NAME or p.clan.lider_id != p.id:
        raise HTTPException(403, "Só o líder do clã trilha um dos três caminhos.")
    if p.caminho:
        raise HTTPException(409, "Você já escolheu seu caminho.")
    if payload.caminho == "reliquia" and p.clan.reliquia_dada:
        raise HTTPException(409, "A relíquia deste clã já foi entregue a outro líder.")
    p.caminho, p.caminho_prog, p.caminho_ok = payload.caminho, 0, False
    db.commit()
    return {"ok": True}


@router.post("/api/clan/caminho/concluir")
def caminho_concluir(payload: RestReq, db: Session = Depends(get_db)):
    """Entrega a recompensa do caminho: relíquia única, poder permanente ou técnica secreta de combate."""
    p = get_player(db, payload.telegram_id, lock=True)
    clan = p.clan
    if clan.name == NO_CLAN_NAME or clan.lider_id != p.id or not p.caminho or p.caminho_ok:
        raise HTTPException(409, "Não há caminho em andamento.")
    if p.caminho_prog < CAMINHO_CHEFES:
        raise HTTPException(400, f"Derrote {CAMINHO_CHEFES} chefes de mapa primeiro ({p.caminho_prog} feitos).")
    custo = CAMINHO_CUSTO[p.caminho]
    if p.ryos < custo:
        raise HTTPException(400, f"Ryos insuficientes: o tributo é {custo}.")
    d = CLAN_DATA[clan.name]
    if p.caminho == "reliquia":
        if clan.reliquia_dada:
            raise HTTPException(409, "A relíquia deste clã já foi entregue a outro líder.")
        if not bolsa_livre(p):
            raise HTTPException(409, f"Mochila cheia ({BOLSA_MAX} itens). Abra espaço para receber a relíquia.")
        db.add(InventoryItem(player_id=p.id, item_name=d["reliquia"], item_type=ItemType(d["tipo"]),
                             status_bonus=dict(d["rel_bonus"])))
        clan.reliquia_dada = True
        msg = f"Você forjou {d['reliquia']}, única no mundo. Está na sua mochila."
    elif p.caminho == "poder":
        ganhos = {k: max(1, v * 2 // 3) for k, v in d["rel_bonus"].items()}
        for k, n in ganhos.items():
            dar_atributo(p, k, n)
        msg = "Seu poder cresceu para sempre: " + ", ".join(f"+{n} {k}" for k, n in ganhos.items()) + "."
    else:
        p.tecnica = d["tecnica"]
        msg = f"Você domina a técnica secreta {d['tecnica']}. Use-a em qualquer luta."
    p.ryos -= custo
    p.caminho_ok = True
    p.fama += 50
    db.commit()
    return {"mensagem": msg}


# ───────── Lojas: itens por nível, poções, venda ao NPC e mercado entre jogadores ─────────
TIER_NIVEL = [1, 5, 10, 16, 24]  # acompanha as faixas dos mapas
TIER_PRECO = [150, 600, 1800, 4500, 10000]
_NOMES = {"Arma": ["Kunai de Treino", "Shuriken de Aço", "Lâmina Curva", "Katana de Tempestade", "Lâmina do Eclipse"],
          "Armadura": ["Colete de Pano", "Couraça de Couro", "Manto Reforçado", "Armadura de Escamas", "Traje de Obsidiana"],
          "Pergaminho": ["Pergaminho de Foco", "Selo de Concentração", "Rolo do Vento", "Grimório Cinzento", "Tomo do Abismo"]}
_BONUS = {"Arma": lambda t: {"forca": 2 * t + 1, "destreza": t + 1},
          "Armadura": lambda t: {"defesa": 2 * t + 1, "agilidade": t},
          "Pergaminho": lambda t: {"inteligencia": 2 * t + 1, "ninjutsu": t + 1, "genjutsu": t + 1}}
_NOMES.update({"Botas": ["Sandálias de Palha", "Botas de Couro", "Botas Ninja", "Botas do Vento", "Botas das Sombras"],
               "Luvas": ["Luvas de Treino", "Luvas Acolchoadas", "Manoplas de Ferro", "Luvas do Trovão", "Garras do Eclipse"],
               "Colar": ["Cordão de Contas", "Amuleto de Jade", "Colar da Lua", "Pingente do Dragão", "Gargantilha Etérea"],
               "Anel": ["Anel de Cobre", "Anel de Prata", "Anel do Foco", "Anel Rubro", "Anel do Vazio"]})
_BONUS.update({"Botas": lambda t: {"agilidade": 2 * t + 1, "defesa": t},
               "Luvas": lambda t: {"destreza": 2 * t + 1, "taijutsu": t + 1},
               "Colar": lambda t: {"inteligencia": t + 1, "ninjutsu": t + 1, "defesa": t},
               "Anel": lambda t: {"genjutsu": t + 1, "destreza": t + 1, "forca": t}})
ITENS_LOJA = {f"{tipo.lower()}_{i + 1}": {"id": f"{tipo.lower()}_{i + 1}", "nome": _NOMES[tipo][i], "tipo": tipo,
                                          "nivel": TIER_NIVEL[i], "preco": TIER_PRECO[i], "bonus": _BONUS[tipo](i + 1)}
              for tipo in _NOMES for i in range(5)}
PRECO_POR_NOME = {v["nome"]: v["preco"] for v in ITENS_LOJA.values()}
RELIQUIAS = {d["reliquia"] for d in CLAN_DATA.values()}  # únicas no mundo: só circulam no mercado entre jogadores
# id: (nome, efeito, valor, preço, nível mínimo)
POCOES = {"vida_p": ("Pílula de Vida", "vida", 60, 40, 1), "vida_m": ("Elixir de Vida", "vida", 200, 150, 10),
          "vida_g": ("Néctar de Vida", "vida", 600, 500, 20), "chakra_p": ("Pílula de Chakra", "chakra", 40, 40, 1),
          "chakra_m": ("Elixir de Chakra", "chakra", 120, 150, 10), "chakra_g": ("Néctar de Chakra", "chakra", 400, 500, 20),
          "energia": ("Pílula de Energia", "energia", 5, 300, 5), "antidoto": ("Antídoto", "cura", 0, 80, 1)}
TAXA_MERCADO = 0.05  # taxa sobre cada venda entre jogadores (some do jogo)
MAX_ANUNCIOS = 10


class CompraReq(BaseModel):
    telegram_id: int = Field(gt=0)
    produto: str
    qtd: int = Field(default=1, ge=1, le=20)


class ItemReq(BaseModel):
    telegram_id: int = Field(gt=0)
    item_id: int


class AnunciarReq(ItemReq):
    preco: int = Field(ge=1, le=1_000_000)


class AnuncioReq(BaseModel):
    telegram_id: int = Field(gt=0)
    anuncio_id: int


class PocaoReq(BaseModel):
    telegram_id: int = Field(gt=0)
    pocao: str


def valor_venda(item: InventoryItem) -> int:
    if item.item_name in PRECO_POR_NOME:
        return int(PRECO_POR_NOME[item.item_name] * 0.4)
    # chaves que começam com "_" (refino da Forja) não são atributos
    return 12 * sum(v for k, v in (item.status_bonus or {}).items() if not k.startswith("_") and isinstance(v, (int, float)))


def usar_pocao(p: Player, pid: str) -> str:
    if pid not in POCOES:
        raise HTTPException(404, "Poção desconhecida.")
    qtd = (p.pocoes or {}).get(pid, 0)
    if qtd <= 0:
        raise HTTPException(400, "Você não tem essa poção.")
    nome, ef, val, _, _ = POCOES[pid]
    if ef == "cura":  # antídoto fora de luta: limpa os status negativos que seguem com o jogador
        if not p.status_fora:
            raise HTTPException(400, "Você não está com nenhum status negativo.")
        p.status_fora = []
        p.pocoes = {**p.pocoes, pid: qtd - 1}
        return f"{nome}: status curados"
    if ef == "vida":
        ganho, p.vida_atual = min(val, p.vida_max - p.vida_atual), min(p.vida_max, p.vida_atual + val)
    elif ef == "chakra":
        ganho, p.chakra_atual = min(val, p.chakra_max - p.chakra_atual), min(p.chakra_max, p.chakra_atual + val)
    else:
        ganho, p.energia = min(val, 25 - p.energia), min(25, p.energia + val)
    if ganho <= 0:
        raise HTTPException(400, "Não faria efeito: o recurso já está cheio.")
    p.pocoes = {**p.pocoes, pid: qtd - 1}
    return f"{nome}: +{ganho} de {ef}"


@router.get("/api/player/{telegram_id}/loja")
def loja_info(telegram_id: int, db: Session = Depends(get_db)):
    p = get_player(db, telegram_id)
    anuncios = db.scalars(select(MarketListing).order_by(MarketListing.id.desc()).limit(50)).all()
    nomes = {x.id: x.ninja_name for x in db.scalars(select(Player).where(Player.id.in_({a.seller_id for a in anuncios} or {-1})))}
    return {"itens": [{**v, "pode": p.level >= v["nivel"]} for v in ITENS_LOJA.values()],
            "pocoes": [{"id": k, "nome": n, "efeito": e, "valor": v, "preco": pr, "nivel": nv, "pode": p.level >= nv,
                        "qtd": (p.pocoes or {}).get(k, 0)} for k, (n, e, v, pr, nv) in POCOES.items()],
            "inventario": [{"id": i.id, "nome": i.item_name, "tipo": i.item_type.value, "bonus": i.status_bonus or {},
                            "equipado": i.is_equipped, "valor": valor_venda(i), "reliquia": i.item_name in RELIQUIAS}
                           for i in p.inventory],
            "mercado": [{"id": a.id, "nome": a.item_name, "tipo": a.item_type, "bonus": a.bonus, "preco": a.preco,
                         "vendedor": nomes.get(a.seller_id, "?"), "meu": a.seller_id == p.id} for a in anuncios],
            "taxa": int(TAXA_MERCADO * 100), "max_anuncios": MAX_ANUNCIOS}


@router.post("/api/loja/comprar")
def loja_comprar(payload: CompraReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    if p.luta:
        raise HTTPException(409, "Termine a luta antes de comprar.")
    if payload.produto in ITENS_LOJA:
        it = ITENS_LOJA[payload.produto]
        if not bolsa_livre(p):
            raise HTTPException(409, f"Mochila cheia ({BOLSA_MAX} itens). Venda ou anuncie algo antes.")
        if p.level < it["nivel"]:
            raise HTTPException(403, f"Exige nível {it['nivel']}.")
        if p.ryos < it["preco"]:
            raise HTTPException(400, f"Ryos insuficientes: custa {it['preco']}.")
        p.ryos -= it["preco"]
        db.add(InventoryItem(player_id=p.id, item_name=it["nome"], item_type=ItemType(it["tipo"]), status_bonus=dict(it["bonus"])))
        msg = f"Você comprou {it['nome']}."
    elif payload.produto in POCOES:
        nome, _, _, preco, nivel = POCOES[payload.produto]
        if p.level < nivel:
            raise HTTPException(403, f"Exige nível {nivel}.")
        if p.ryos < preco * payload.qtd:
            raise HTTPException(400, f"Ryos insuficientes: custa {preco * payload.qtd}.")
        p.ryos -= preco * payload.qtd
        p.pocoes = {**(p.pocoes or {}), payload.produto: (p.pocoes or {}).get(payload.produto, 0) + payload.qtd}
        msg = f"Você comprou {payload.qtd}x {nome}."
    else:
        raise HTTPException(404, "Produto não encontrado.")
    db.commit()
    return {"mensagem": msg}


@router.post("/api/pocao/usar")
def pocao_usar(payload: PocaoReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    if p.luta:
        raise HTTPException(409, "Durante a luta, use a poção pelo painel de combate.")
    msg = usar_pocao(p, payload.pocao)
    db.commit()
    return {"mensagem": msg}


def _item_proprio(db: Session, p: Player, item_id: int) -> InventoryItem:
    it = db.get(InventoryItem, item_id)
    if it is None or it.player_id != p.id:
        raise HTTPException(404, "Item não encontrado na sua mochila.")
    if it.is_equipped:
        raise HTTPException(409, "Desequipe o item antes.")
    return it


@router.post("/api/loja/vender")
def loja_vender(payload: ItemReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    it = _item_proprio(db, p, payload.item_id)
    if it.item_name in RELIQUIAS:
        raise HTTPException(403, "O mercador não compra relíquias de clã. Anuncie no mercado dos jogadores.")
    valor = valor_venda(it)
    p.ryos += valor
    db.delete(it)
    db.commit()
    return {"mensagem": f"Vendido por {valor} ryos."}


@router.post("/api/item/descartar")
def item_descartar(payload: ItemReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    it = _item_proprio(db, p, payload.item_id)
    if it.item_name in RELIQUIAS:
        raise HTTPException(403, "Relíquias de clã não podem ser jogadas fora.")
    db.delete(it)
    db.commit()
    return {"mensagem": "Item jogado fora."}


@router.post("/api/mercado/anunciar")
def mercado_anunciar(payload: AnunciarReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    it = _item_proprio(db, p, payload.item_id)
    if db.scalar(select(func.count()).select_from(MarketListing).where(MarketListing.seller_id == p.id)) >= MAX_ANUNCIOS:
        raise HTTPException(409, f"Limite de {MAX_ANUNCIOS} anúncios ativos.")
    db.add(MarketListing(seller_id=p.id, item_name=it.item_name, item_type=it.item_type.value,
                         bonus=dict(it.status_bonus or {}), preco=payload.preco))
    db.delete(it)
    db.commit()
    return {"mensagem": f"{it.item_name} anunciado por {payload.preco} ryos."}


@router.post("/api/mercado/comprar")
def mercado_comprar(payload: AnuncioReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    a = db.scalar(select(MarketListing).where(MarketListing.id == payload.anuncio_id).with_for_update())
    if a is None:
        raise HTTPException(404, "Esse anúncio não existe mais.")
    if a.seller_id == p.id:
        raise HTTPException(400, "Este anúncio é seu. Cancele-o para recuperar o item.")
    if p.ryos < a.preco:
        raise HTTPException(400, f"Ryos insuficientes: custa {a.preco}.")
    if not bolsa_livre(p):
        raise HTTPException(409, f"Mochila cheia ({BOLSA_MAX} itens).")
    p.ryos -= a.preco
    seller = db.get(Player, a.seller_id)
    if seller is not None:
        seller.ryos += a.preco - int(a.preco * TAXA_MERCADO)
    db.add(InventoryItem(player_id=p.id, item_name=a.item_name, item_type=ItemType(a.item_type), status_bonus=dict(a.bonus)))
    nome = a.item_name
    db.delete(a)
    db.commit()
    return {"mensagem": f"Você comprou {nome}."}


@router.post("/api/mercado/cancelar")
def mercado_cancelar(payload: AnuncioReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    a = db.scalar(select(MarketListing).where(MarketListing.id == payload.anuncio_id).with_for_update())
    if a is None or a.seller_id != p.id:
        raise HTTPException(404, "Anúncio não encontrado.")
    if not bolsa_livre(p):
        raise HTTPException(409, f"Mochila cheia ({BOLSA_MAX} itens). Abra espaço para recuperar o item.")
    db.add(InventoryItem(player_id=p.id, item_name=a.item_name, item_type=ItemType(a.item_type), status_bonus=dict(a.bonus)))
    db.delete(a)
    db.commit()
    return {"mensagem": "Anúncio cancelado: o item voltou para a mochila."}


# ───────── Pets: ovos, naturezas, afinidade e loja pet ─────────
AFINIDADE_MAX = 5000
RARIDADE = ["Comum", "Incomum", "Raro", "Lendário"]
MAX_PETS = 20  # teto absoluto; o limite de cada jogador é Player.pet_slots (2 iniciais, +1 por 100 Ouro Ninja)


def pet_limite(p) -> int:
    return min(MAX_PETS, max(2, p.pet_slots or 0))
# id: (nome, raridade, preço, nível mínimo)
OVOS = {"ovo_comum": ("Ovo Comum", 0, 200, 1), "ovo_incomum": ("Ovo Incomum", 1, 900, 5),
        "ovo_raro": ("Ovo Raro", 2, 3500, 12), "ovo_lendario": ("Ovo Lendário", 3, 15000, 20)}
ESPECIES = {0: ["Coelho Ninja", "Pardal Mensageiro", "Rato de Telhado", "Sapo do Brejo"],
            1: ["Raposa Cinzenta", "Corvo Sombrio", "Texugo de Ferro"],
            2: ["Lobo da Neve", "Serpente Rubra", "Águia de Trovão"],
            3: ["Dragão Menor", "Tigre Espectral", "Fênix de Cinzas"]}
# (natureza, atributo, bônus base, peso, dificuldade). Ovos melhores multiplicam o peso das naturezas difíceis.
NATUREZAS = [("Feroz", "forca", 3, 40, 0), ("Veloz", "agilidade", 3, 40, 0), ("Resistente", "defesa", 3, 40, 0),
             ("Astuta", "inteligencia", 4, 20, 1), ("Certeira", "destreza", 4, 20, 1),
             ("Chamejante", "ninjutsu", 6, 8, 2), ("Sombria", "genjutsu", 6, 8, 2), ("Lutadora", "taijutsu", 6, 8, 2),
             ("Vital", "vigor", 8, 2, 3), ("Ancestral", "chakra_attr", 8, 2, 3)]
NAT_POR_NOME = {n[0]: n for n in NATUREZAS}
BOOST_OVO = [1, 1.6, 3, 6]  # quanto o ovo favorece naturezas difíceis
MULT_OVO = [1, 1.3, 1.7, 2.2]  # multiplicador do bônus pela raridade do pet
# id: (nome, afinidade, preço)
ALIMENTOS = {"racao": ("Ração Simples", 50, 20), "petisco": ("Petisco Nobre", 200, 70), "banquete": ("Banquete Lendário", 600, 200)}


def _pesos(tier: int) -> list:
    return [n[3] * BOOST_OVO[tier] ** n[4] for n in NATUREZAS]


def chances_natureza(tier: int) -> list:
    ps = _pesos(tier)
    return [(n[0], round(100 * w / sum(ps), 1)) for n, w in zip(NATUREZAS, ps)]


def bonus_pet(pet: Pet):
    _, attr, base, _, _ = NAT_POR_NOME[pet.natureza]
    return attr, max(1, round(base * MULT_OVO[pet.raridade]))


def chance_pet(afinidade: int) -> float:
    return 0.10 + 0.90 * afinidade / AFINIDADE_MAX


def pet_afinidade(db: Session, L: dict) -> None:
    """Pet que acompanhou a luta ganha afinidade pela vitória (chefes valem mais)."""
    pet = db.get(Pet, L["pet_id"]) if L.get("pet_id") else None
    if pet:
        pet.afinidade = min(AFINIDADE_MAX, pet.afinidade + (3 if L["boss"] else 1))


class PetCompraReq(BaseModel):
    telegram_id: int = Field(gt=0)
    produto: str
    qtd: int = Field(default=1, ge=1, le=20)


class OvoReq(BaseModel):
    telegram_id: int = Field(gt=0)
    ovo: str


class PetReq(BaseModel):
    telegram_id: int = Field(gt=0)
    pet_id: int


class AlimentarReq(PetReq):
    alimento: str


def _pet_proprio(db: Session, p: Player, pet_id: int) -> Pet:
    pet = db.get(Pet, pet_id)
    if pet is None or pet.player_id != p.id:
        raise HTTPException(404, "Pet não encontrado.")
    return pet


@router.get("/api/player/{telegram_id}/pets")
def pets_info(telegram_id: int, db: Session = Depends(get_db)):
    p = get_player(db, telegram_id)
    pets = db.scalars(select(Pet).where(Pet.player_id == p.id).order_by(Pet.ativo.desc(), Pet.raridade.desc(), Pet.afinidade.desc())).all()
    out = []
    for x in pets:
        attr, n = bonus_pet(x)
        out.append({"id": x.id, "especie": x.especie, "raridade": RARIDADE[x.raridade], "tier": x.raridade, "natureza": x.natureza,
                    "atributo": attr, "bonus": n, "afinidade": x.afinidade, "chance": round(100 * chance_pet(x.afinidade)), "ativo": x.ativo})
    return {"pets": out, "max": pet_limite(p), "afinidade_max": AFINIDADE_MAX,
            "ovos": [{"id": k, "nome": n, "tier": t, "preco": pr, "nivel": nv, "pode": p.level >= nv, "qtd": (p.ovos or {}).get(k, 0),
                      "especies": ESPECIES[t], "naturezas": chances_natureza(t)} for k, (n, t, pr, nv) in OVOS.items()],
            "alimentos": [{"id": k, "nome": n, "valor": v, "preco": pr, "qtd": (p.alimentos or {}).get(k, 0)}
                          for k, (n, v, pr) in ALIMENTOS.items()]}


@router.post("/api/pet/comprar")
def pet_comprar(payload: PetCompraReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    if payload.produto in OVOS:
        nome, _, preco, nivel = OVOS[payload.produto]
        if p.level < nivel:
            raise HTTPException(403, f"Exige nível {nivel}.")
        campo = "ovos"
    elif payload.produto in ALIMENTOS:
        nome, _, preco = ALIMENTOS[payload.produto]
        campo = "alimentos"
    else:
        raise HTTPException(404, "Produto não encontrado.")
    if p.ryos < preco * payload.qtd:
        raise HTTPException(400, f"Ryos insuficientes: custa {preco * payload.qtd}.")
    p.ryos -= preco * payload.qtd
    estoque = dict(getattr(p, campo) or {})
    estoque[payload.produto] = estoque.get(payload.produto, 0) + payload.qtd
    setattr(p, campo, estoque)
    db.commit()
    return {"mensagem": f"Você comprou {payload.qtd}x {nome}."}


@router.post("/api/pet/chocar")
def pet_chocar(payload: OvoReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    if payload.ovo not in OVOS:
        raise HTTPException(404, "Ovo desconhecido.")
    qtd = (p.ovos or {}).get(payload.ovo, 0)
    if qtd <= 0:
        raise HTTPException(400, "Você não tem esse ovo.")
    total = db.scalar(select(func.count()).select_from(Pet).where(Pet.player_id == p.id))
    if total >= pet_limite(p):
        raise HTTPException(409, f"Seus {pet_limite(p)} slots de pet estão cheios. Solte um pet ou compre mais slots com Ouro Ninja.")
    _, tier, _, _ = OVOS[payload.ovo]
    especie = random.choice(ESPECIES[tier])
    nat = random.choices(NATUREZAS, weights=_pesos(tier))[0]
    pet = Pet(player_id=p.id, especie=especie, raridade=tier, natureza=nat[0], afinidade=0, ativo=total == 0)
    db.add(pet)
    p.ovos = {**p.ovos, payload.ovo: qtd - 1}
    db.commit()
    attr, n = bonus_pet(pet)
    return {"mensagem": f"Nasceu: {especie} {nat[0]} ({RARIDADE[tier]}). Dá +{n} de {attr} quando ativa o bônus."}


@router.post("/api/pet/alimentar")
def pet_alimentar(payload: AlimentarReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    pet = _pet_proprio(db, p, payload.pet_id)
    if payload.alimento not in ALIMENTOS:
        raise HTTPException(404, "Alimento desconhecido.")
    qtd = (p.alimentos or {}).get(payload.alimento, 0)
    if qtd <= 0:
        raise HTTPException(400, "Você não tem esse alimento.")
    if pet.afinidade >= AFINIDADE_MAX:
        raise HTTPException(400, "A afinidade já está no máximo.")
    nome, valor, _ = ALIMENTOS[payload.alimento]
    ganho = min(valor, AFINIDADE_MAX - pet.afinidade)
    pet.afinidade += ganho
    p.alimentos = {**p.alimentos, payload.alimento: qtd - 1}
    db.commit()
    return {"mensagem": f"{pet.especie} comeu {nome}: +{ganho} de afinidade."}


@router.post("/api/pet/ativar")
def pet_ativar(payload: PetReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    alvo = _pet_proprio(db, p, payload.pet_id)
    if p.luta:
        raise HTTPException(409, "Troque de pet depois da luta.")
    for x in db.scalars(select(Pet).where(Pet.player_id == p.id)):
        x.ativo = x.id == alvo.id
    db.commit()
    return {"ok": True}


@router.post("/api/pet/soltar")
def pet_soltar(payload: PetReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    pet = _pet_proprio(db, p, payload.pet_id)
    if p.luta:
        raise HTTPException(409, "Termine a luta antes.")
    nome = pet.especie
    db.delete(pet)
    db.commit()
    return {"mensagem": f"{nome} voltou à natureza."}


# ───────── Esqueleto novo: bolsa (30 itens), patente e Hospital ─────────
BOLSA_MAX = 30  # equipamentos, materiais e itens de quest; poções empilham fora desse limite
SLOTS = ["Arma", "Armadura", "Botas", "Luvas", "Colar", "Anel", "Anel", "Pergaminho"]  # dois anéis
MAX_EQUIP = {"Anel": 2}  # tipos que aceitam mais de um item equipado
HOSPITAL_GRATIS_CD = 600  # segundos entre curas gratuitas
BUFFS_HOSPITAL = [("Bênção do Tigre", "forca"), ("Bênção do Falcão", "agilidade"), ("Bênção da Tartaruga", "defesa"),
                  ("Bênção da Serpente", "genjutsu"), ("Bênção da Fênix", "ninjutsu"), ("Bênção do Urso", "taijutsu"),
                  ("Bênção da Coruja", "inteligencia"), ("Bênção do Lince", "destreza")]


def bolsa_livre(p: Player, extra: int = 0) -> bool:
    return len(p.inventory) + extra < BOLSA_MAX


def _n(x: int) -> str:
    return f"{x:,}".replace(",", ".")


def patente_info(p: Player) -> dict:
    """O que falta para a próxima patente: exame (nível, fama e missão da Jornada) ou, a partir de Anbu de Elite, fama e cadeira."""
    atual = rank_titulo(p)
    for seq, (nome, nivel, alvo, fama) in sorted(EXAMES.items()):
        if posto(p) < PATENTES.index(alvo):
            req = f"nível {nivel} (você: {p.level})" + (f", {_n(fama)} de fama (você: {_n(p.fama)})" if fama else "")
            return {"atual": atual, "proxima": alvo, "texto": f"{nome}: chegue ao {req} e à missão {seq} da Jornada (você: {p.journey + 1})."}
    if p.rank == NinjaRank.ANBU:
        return {"atual": atual, "proxima": "Sannin",
                "texto": f"Sannin: {_n(SANNIN_MIN_FAMA)} de fama e uma cadeira livre ou mais fama que o Sannin mais fraco por 7 dias (sua fama: {_n(p.fama)})."}
    if p.rank == NinjaRank.SANNIN:
        return {"atual": atual, "proxima": "Kage",
                "texto": f"Kage: {_n(KAGE_MIN_FAMA)} de fama e mais fama que o Kage da vila por 7 dias (sua fama: {_n(p.fama)}). Só na sua vila de origem."}
    return {"atual": atual, "proxima": None, "texto": "Você alcançou a mais alta patente."}


class NacReq(BaseModel):
    telegram_id: int = Field(gt=0)
    village_id: int = Field(gt=0)


@router.post("/api/nacionalidade/trocar")
def trocar_nacionalidade(payload: NacReq, db: Session = Depends(get_db)):
    """Troca de vila. A fama zera (ela pertence à vila), a missão atual recomeça e o clã de outra vila fica para trás.
    Kage e Sannin não podem trocar: precisam deixar o cargo primeiro."""
    p = get_player(db, payload.telegram_id, lock=True)
    novo = db.get(Village, payload.village_id)
    if p.luta:
        raise HTTPException(409, "Termine a luta antes.")
    if novo is None:
        raise HTTPException(404, "Vila não encontrada.")
    if novo.id == p.village_id:
        raise HTTPException(400, "Você já pertence a esta vila.")
    if p.rank in (NinjaRank.KAGE, NinjaRank.SANNIN):
        raise HTTPException(403, "Kage e Sannin não podem trocar de vila.")
    antiga = p.village
    if antiga.challenger_id == p.id:
        antiga.challenger_id = antiga.challenger_since = None
    ids = [v.id for v in db.scalars(select(Village).order_by(Village.id))]
    if p.clan.name != NO_CLAN_NAME and CLAN_DATA.get(p.clan.name, {}).get("vila") != ids.index(novo.id):
        if p.clan.lider_id == p.id:
            p.clan.lider_id = None
        p.clan_id = db.scalar(select(Clan.id).where(Clan.name == NO_CLAN_NAME))
        p.clan_pontos, p.caminho, p.caminho_prog, p.caminho_ok = 0, None, 0, False
    p.prov_clan_id, p.prov_etapa, p.prov_prog = None, 0, 0
    perdida = p.fama
    p.village_id, p.fama, p.journey_prog, p.sannin_desde = novo.id, 0, 0, None
    db.commit()
    return {"mensagem": f"Você agora é de {novo.name}. Sua fama ({_n(perdida)}) foi zerada.", "fama_perdida": perdida}


def hospital_info(p: Player) -> dict:
    now = datetime.now(timezone.utc)
    cd = max(0, HOSPITAL_GRATIS_CD - int((now - as_utc(p.hospital_gratis_at)).total_seconds())) if p.hospital_gratis_at else 0
    buff = None
    if p.buff:
        resto = int((datetime.fromisoformat(p.buff["ate"]) - now).total_seconds())
        if resto > 0:
            buff = {"nome": p.buff["nome"], "attr": p.buff["attr"], "pct": p.buff["pct"], "restante_s": resto}
    return {"gratis_restante_s": cd, "preco_total": 40 + 12 * p.level, "preco_sannin": 150 + 40 * p.level, "buff": buff}


class HospitalReq(BaseModel):
    telegram_id: int = Field(gt=0)
    opcao: Literal["gratis", "total", "sannin"]


@router.post("/api/hospital")
def hospital(payload: HospitalReq, db: Session = Depends(get_db)):
    """Gratuita: 50% do HP perdido (a cada 10 min). Total: HP e chakra. Sannin: total e buff aleatório de 15 min."""
    p = get_player(db, payload.telegram_id, lock=True)
    if p.luta:
        raise HTTPException(409, "O hospital não atende no meio de uma luta.")
    if p.masmorra:
        raise HTTPException(409, "O hospital fica fechado durante a masmorra: use os itens da bolsa ou abandone-a.")
    if p.torre_sessao:
        raise HTTPException(409, "A Torre não tem hospital: use os itens da bolsa ou saia da Torre.")
    if (p.nk_fama or 0) >= 1000:
        raise HTTPException(403, "Você foi expulso da aldeia como Nukenin: o hospital não atende quem está no Livro Bingo.")
    now = datetime.now(timezone.utc)
    info = hospital_info(p)
    p.status_fora = []  # o hospital cura qualquer status negativo
    if payload.opcao == "gratis":
        falta = p.vida_max - p.vida_atual
        if falta <= 0:
            raise HTTPException(400, "Você não está ferido.")
        if info["gratis_restante_s"] > 0:
            raise HTTPException(429, f"A cura gratuita volta em {(info['gratis_restante_s'] + 59) // 60} min.")
        cura = max(1, falta // 2)
        p.vida_atual += cura
        p.hospital_gratis_at = now
        msg = f"Você recuperou {cura} de vida."
    else:
        preco = info["preco_total" if payload.opcao == "total" else "preco_sannin"]
        if p.ryos < preco:
            raise HTTPException(400, f"Ryos insuficientes: custa {preco}.")
        p.ryos -= preco
        p.vida_atual, p.chakra_atual = p.vida_max, p.chakra_max
        msg = "Vida e chakra totalmente recuperados."
        if payload.opcao == "sannin":
            nome, attr = random.choice(BUFFS_HOSPITAL)
            p.buff = {"nome": nome, "attr": attr, "pct": 0.15, "ate": (now + timedelta(minutes=15)).isoformat()}
            msg += f" {nome}: +15% de {attr} por 15 minutos."
    db.commit()
    return {"mensagem": msg}


@router.get("/api/player/{telegram_id}/bolsa")
def bolsa(telegram_id: int, db: Session = Depends(get_db)):
    """Mochila por abas: equipados, equipamentos em standby, consumíveis, materiais e itens de quest."""
    p = get_player(db, telegram_id)
    vi = vila_idx(p)
    mis = mission_for(p.journey + 1, vi)
    quests = []
    if mis["tipo"] == "coleta" and p.level >= mis["req_nivel"]:
        quests.append({"nome": recurso_vila(vi, mis["mapa"]), "qtd": p.journey_prog, "meta": mis["qtd"],
                       "mapa": mapa_vila(mis["mapa"], vi)["nome"]})
    return {"max": BOLSA_MAX, "slots": SLOTS,
            "itens": [{"id": i.id, "nome": i.item_name, "tipo": i.item_type.value, "bonus": i.status_bonus or {},
                       "equipado": i.is_equipped} for i in p.inventory],
            "consumiveis": [{"id": k, "nome": POCOES[k][0], "efeito": POCOES[k][1], "valor": POCOES[k][2], "qtd": q}
                            for k, q in (p.pocoes or {}).items() if q > 0 and k in POCOES],
            "materiais": materiais_view(p, vi),
            "quests": quests}


# ───────── Elementos e jutsus com nome ─────────
VILA_ELEMENTO = ["fogo", "agua", "vento", "agua", "raio", "terra", None]  # a Lua Cinzenta não tem elemento fixo


def elem_mapa(vi: int) -> str:
    """Elemento de um inimigo: 70% o da vila, senão sorteado (a Lua Cinzenta é sempre sorteada)."""
    base = VILA_ELEMENTO[vi % len(VILA_ELEMENTO)]
    return base if base and random.random() < 0.7 else random.choice(list(ELEMENTOS))


def ofertas(p: Player, i: int) -> list:
    """As duas opções do marco `i`: ninjutsu de um dos seus elementos (o menos desenvolvido) e um jutsu sem elemento
    (genjutsu se esse for seu estilo, senão taijutsu)."""
    els = [p.elemento, *(p.elementos_extra or [])]
    usados = [JUTSUS[j]["elem"] for j in (p.jutsus or {}).values() if j in JUTSUS]
    e = min(els, key=lambda x: (usados.count(x), els.index(x)))
    cat = "genjutsu" if p.genjutsu > p.taijutsu else "taijutsu"
    return [JUTSUS[f"ninjutsu_{e}_{i + 1}"], JUTSUS[f"{cat}_x_{i + 1}"]]


def jutsus_info(p: Player) -> dict:
    marcos = []
    for i, nv in enumerate(MARCOS):
        esc = (p.jutsus or {}).get(str(nv))
        estado = "escolhido" if esc else "pendente" if p.level >= nv else "bloqueado"
        marcos.append({"nivel": nv, "estado": estado, "escolhido": JUTSUS[esc] if esc else None,
                       "opcoes": ofertas(p, i) if estado == "pendente" else []})
    return {"elemento": p.elemento, "extras": list(p.elementos_extra or []), "marcos": marcos}


class JutsuReq(BaseModel):
    telegram_id: int = Field(gt=0)
    nivel: int
    jutsu_id: str


@router.post("/api/jutsu/escolher")
def jutsu_escolher(payload: JutsuReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    if payload.nivel not in MARCOS or p.level < payload.nivel:
        raise HTTPException(403, "Esse marco ainda não foi alcançado.")
    if str(payload.nivel) in (p.jutsus or {}):
        raise HTTPException(409, "Você já escolheu o jutsu deste nível.")
    if payload.jutsu_id not in [o["id"] for o in ofertas(p, MARCOS.index(payload.nivel))]:
        raise HTTPException(400, "Essa opção não está disponível para você.")
    p.jutsus = {**(p.jutsus or {}), str(payload.nivel): payload.jutsu_id}
    db.commit()
    return {"mensagem": f"Você aprendeu {JUTSUS[payload.jutsu_id]['nome']}."}


# ───────── Forja: materiais, refino, desmontar e criar equipamentos ─────────
# Materiais caem de vitórias nos mapas e empilham fora da mochila. O tier do material acompanha o perigo do mapa
# (mapas 1-3 = tier 1, 4-6 = tier 2, ... 13-15 = tier 5).
MAT_NOME = {"mat_1": "Lascas de Ferro", "mat_2": "Aço Temperado", "mat_3": "Cristal de Chakra",
            "mat_4": "Essência Elemental", "mat_5": "Fragmento Celestial"}
REFINO_MAX = 10
REFINO_CHANCE = [1.0, 1.0, 1.0, 0.85, 0.70, 0.55, 0.40, 0.30, 0.20, 0.10]  # chance de cada degrau (+1 a +10)
REFINO_PASSO = 0.10  # cada refino soma 10% do bônus original de cada atributo (mínimo +1)


def mat_do_mapa(perigo: int) -> str:
    return f"mat_{min(5, (perigo - 1) // 3 + 1)}"


def dropar_materiais(p: Player, mp: dict, boss: bool, mult: float) -> list:
    """Sorteia materiais após uma vitória. Chefes sempre dão (3 a 5); lutas comuns têm ~35% de chance (1 a 2)."""
    if not boss and random.random() >= min(0.9, 0.35 * (0.6 + 0.4 * mult)):
        return []
    mid, qtd = mat_do_mapa(mp["perigo"]), random.randint(3, 5) if boss else random.randint(1, 2)
    est = dict(p.materiais or {})
    est[mid] = est.get(mid, 0) + qtd
    p.materiais = est
    return [f"{qtd}x {MAT_NOME[mid]}"]


def _gasta_material(p: Player, mid: str, qtd: int) -> None:
    est = dict(p.materiais or {})
    est[mid] = est.get(mid, 0) - qtd
    p.materiais = est


def forja_base(it: InventoryItem) -> dict:
    """Bônus original do item (sem refino). Itens nunca refinados ainda não têm o campo _base."""
    b = it.status_bonus or {}
    return dict(b["_base"]) if isinstance(b.get("_base"), dict) else {k: v for k, v in b.items() if not k.startswith("_") and isinstance(v, (int, float))}


def forja_refino(it: InventoryItem) -> int:
    return int((it.status_bonus or {}).get("_ref", 0))


def forja_tier(it: InventoryItem) -> int:
    """Tier do item (1 a 5): o gravado na criação (itens forjados) ou, senão, o do maior bônus ORIGINAL. O refino não muda o tier."""
    t = (it.status_bonus or {}).get("_tier")
    return int(t) if t else min(5, 1 + int(max(forja_base(it).values(), default=1)) // 8)


def forja_rendimento(it: InventoryItem) -> int:
    """Materiais devolvidos ao desmontar: cresce com o tier, as estrelas ★ e o refino."""
    return 1 + forja_tier(it) + 2 * it.item_name.count("★") + forja_refino(it) // 2


def refino_proximo(it: InventoryItem) -> Optional[dict]:
    n = forja_refino(it) + 1
    if n > REFINO_MAX or it.item_name in RELIQUIAS:
        return None
    t = forja_tier(it)
    return {"nivel": n, "chance": round(100 * REFINO_CHANCE[n - 1]), "ryos": 60 * n * t, "mat": f"mat_{t}", "mat_nome": MAT_NOME[f"mat_{t}"], "qtd": n + 1}


def aplicar_refino(it: InventoryItem, n: int) -> None:
    base = forja_base(it)
    novo = {k: v + n * max(1, round(v * REFINO_PASSO)) for k, v in base.items()}
    extra = {"_tier": it.status_bonus["_tier"]} if (it.status_bonus or {}).get("_tier") else {}
    it.status_bonus = {**novo, **extra, "_base": base, "_ref": n}  # objeto novo: o SQLAlchemy só persiste JSON reatribuído


# Receitas: 7 tipos x 5 tiers. Cobrem o que a Loja não vende (ela para no nível 24) e usam a mesma tabela de bônus, em degraus acima.
FORJA_NIVEL, FORJA_RYOS, FORJA_QTD = [20, 35, 50, 70, 90], [1500, 4000, 9000, 20000, 45000], [8, 10, 12, 14, 16]
FORJA_BASE = {"Arma": "Lâmina", "Armadura": "Armadura", "Pergaminho": "Pergaminho", "Botas": "Botas", "Luvas": "Luvas", "Colar": "Colar", "Anel": "Anel"}
FORJA_SUFIXO = ["de Ferro", "de Aço", "de Cristal", "Elemental", "Celestial"]
RECEITAS = {f"{tipo.lower()}_f{t}": {"id": f"{tipo.lower()}_f{t}", "tipo": tipo, "tier": t, "nome": f"{FORJA_BASE[tipo]} {FORJA_SUFIXO[t - 1]}",
                                     "nivel": FORJA_NIVEL[t - 1], "ryos": FORJA_RYOS[t - 1], "mat": f"mat_{t}", "qtd": FORJA_QTD[t - 1],
                                     "bonus": _BONUS[tipo](t + 5)}
            for tipo in FORJA_BASE for t in range(1, 6)}


class CriarReq(BaseModel):
    telegram_id: int = Field(gt=0)
    receita: str


def _item_da_forja(p: Player, item_id: int) -> InventoryItem:
    it = next((i for i in p.inventory if i.id == item_id), None)
    if it is None:
        raise HTTPException(404, "Item não encontrado na sua mochila.")
    return it


@router.get("/api/player/{telegram_id}/forja")
def forja_info(telegram_id: int, db: Session = Depends(get_db)):
    p = get_player(db, telegram_id)
    est = p.materiais or {}
    itens = []
    for i in p.inventory:
        rel = i.item_name in RELIQUIAS
        itens.append({"id": i.id, "nome": i.item_name, "tipo": i.item_type.value, "bonus": i.status_bonus or {}, "equipado": i.is_equipped,
                      "refino": forja_refino(i), "tier": forja_tier(i), "reliquia": rel, "prox": refino_proximo(i),
                      "desmonta": None if rel or i.is_equipped else {"mat": f"mat_{forja_tier(i)}", "nome": MAT_NOME[f"mat_{forja_tier(i)}"], "qtd": forja_rendimento(i)}})
    return {"materiais": [{"id": k, "nome": n, "tier": int(k[-1]), "qtd": est.get(k, 0)} for k, n in MAT_NOME.items()],
            "itens": itens, "refino_max": REFINO_MAX, "bolsa": {"usado": len(p.inventory), "max": BOLSA_MAX},
            "receitas": [{**r, "mat_nome": MAT_NOME[r["mat"]], "pode_nivel": p.level >= r["nivel"]} for r in RECEITAS.values()]}


@router.post("/api/forja/refinar")
def forja_refinar(payload: ItemReq, db: Session = Depends(get_db)):
    """Refina +1 (até +10). Cada tentativa gasta ryos e materiais do tier do item; falhar perde o custo, mas o item nunca é destruído."""
    p = get_player(db, payload.telegram_id, lock=True)
    if p.luta:
        raise HTTPException(409, "Termine a luta antes de usar a Forja.")
    it = _item_da_forja(p, payload.item_id)
    if it.item_name in RELIQUIAS:
        raise HTTPException(403, "Relíquias de clã já são únicas e não podem ser refinadas.")
    c = refino_proximo(it)
    if c is None:
        raise HTTPException(400, f"Este item já está no refino máximo (+{REFINO_MAX}).")
    if p.ryos < c["ryos"]:
        raise HTTPException(400, f"Ryos insuficientes: o refino custa {c['ryos']}.")
    if (p.materiais or {}).get(c["mat"], 0) < c["qtd"]:
        raise HTTPException(400, f"Faltam materiais: {c['qtd']}x {c['mat_nome']} (você tem {(p.materiais or {}).get(c['mat'], 0)}).")
    p.ryos -= c["ryos"]
    _gasta_material(p, c["mat"], c["qtd"])
    ok = random.random() < REFINO_CHANCE[c["nivel"] - 1]
    if ok:
        aplicar_refino(it, c["nivel"])
        msg = f"Sucesso! {it.item_name} agora está +{c['nivel']}."
    else:
        msg = f"O refino falhou. Os materiais e os ryos se perderam, mas {it.item_name} segue em +{c['nivel'] - 1}."
    prog(p, "forja")
    db.commit()
    return {"mensagem": msg, "sucesso": ok, "refino": forja_refino(it)}


@router.post("/api/forja/desmontar")
def forja_desmontar(payload: ItemReq, db: Session = Depends(get_db)):
    """Destrói um equipamento fora de uso e devolve materiais do tier dele."""
    p = get_player(db, payload.telegram_id, lock=True)
    if p.luta:
        raise HTTPException(409, "Termine a luta antes de usar a Forja.")
    it = _item_da_forja(p, payload.item_id)
    if it.is_equipped:
        raise HTTPException(409, "Desequipe o item antes.")
    if it.item_name in RELIQUIAS:
        raise HTTPException(403, "Relíquias de clã não podem ser desmontadas.")
    mid, qtd = f"mat_{forja_tier(it)}", forja_rendimento(it)
    nome = it.item_name
    est = dict(p.materiais or {})
    est[mid] = est.get(mid, 0) + qtd
    p.materiais = est
    db.delete(it)
    db.commit()
    return {"mensagem": f"{nome} desmontado: +{qtd}x {MAT_NOME[mid]}."}


@router.post("/api/forja/criar")
def forja_criar(payload: CriarReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    if p.luta:
        raise HTTPException(409, "Termine a luta antes de usar a Forja.")
    r = RECEITAS.get(payload.receita)
    if r is None:
        raise HTTPException(404, "Receita não encontrada.")
    if p.level < r["nivel"]:
        raise HTTPException(403, f"Exige nível {r['nivel']}.")
    if not bolsa_livre(p):
        raise HTTPException(409, f"Mochila cheia ({BOLSA_MAX} itens). Venda, desmonte ou anuncie algo antes.")
    if p.ryos < r["ryos"]:
        raise HTTPException(400, f"Ryos insuficientes: custa {r['ryos']}.")
    if (p.materiais or {}).get(r["mat"], 0) < r["qtd"]:
        raise HTTPException(400, f"Faltam materiais: {r['qtd']}x {MAT_NOME[r['mat']]}.")
    p.ryos -= r["ryos"]
    _gasta_material(p, r["mat"], r["qtd"])
    db.add(InventoryItem(player_id=p.id, item_name=r["nome"], item_type=ItemType(r["tipo"]), status_bonus={**r["bonus"], "_tier": r["tier"]}))
    prog(p, "forja")
    db.commit()
    return {"mensagem": f"Você forjou {r['nome']}."}


# ───────── Dojo: senseis e maestria ─────────
# Sessão com sensei: gasta 1 de energia e ryos, e rende mais pontos que o treino comum (25 ryos por ponto, sem energia).
# Mestres melhores dão mais pontos por energia, mas cobram mais por ponto.
SENSEIS = [{"id": "s1", "nome": "Instrutor Kuro", "nivel": 1, "preco": 40, "ganho": 2},
           {"id": "s2", "nome": "Mestre Hanzo", "nivel": 15, "preco": 100, "ganho": 5},
           {"id": "s3", "nome": "Grão-Mestre Ryuzen", "nivel": 35, "preco": 300, "ganho": 11},
           {"id": "s4", "nome": "Lenda Oboro", "nivel": 60, "preco": 800, "ganho": 22}]
SENSEI_POR_ID = {s["id"]: s for s in SENSEIS}
MAESTRIA_PTS, MAESTRIA_MAX, MAESTRIA_BONUS = 40, 10, 0.05  # pontos por grau, grau máximo, +5% de ganho por grau
XP_POR_PONTO = 6


def maestria_grau(p: Player, attr: str) -> int:
    return min(MAESTRIA_MAX, (p.maestria or {}).get(attr, 0) // MAESTRIA_PTS)


class DojoReq(BaseModel):
    telegram_id: int = Field(gt=0)
    atributo: str
    sensei: str
    sessoes: int = Field(default=1, ge=1, le=5)


@router.get("/api/player/{telegram_id}/dojo")
def dojo_info(telegram_id: int, db: Session = Depends(get_db)):
    p = get_player(db, telegram_id)
    m = p.maestria or {}
    return {"senseis": [{**s, "pode": p.level >= s["nivel"]} for s in SENSEIS],
            "maestria": [{"attr": a, "pontos": m.get(a, 0), "grau": maestria_grau(p, a),
                          "prox": None if maestria_grau(p, a) >= MAESTRIA_MAX else MAESTRIA_PTS - m.get(a, 0) % MAESTRIA_PTS} for a in ATTRS],
            "grau_max": MAESTRIA_MAX, "pts_grau": MAESTRIA_PTS, "bonus_grau": int(MAESTRIA_BONUS * 100)}


@router.post("/api/dojo/treinar")
def dojo_treinar(payload: DojoReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    s = SENSEI_POR_ID.get(payload.sensei)
    if p.luta:
        raise HTTPException(409, "Termine a luta antes de treinar.")
    if payload.atributo not in ATTRS:
        raise HTTPException(400, "Atributo inválido.")
    if s is None:
        raise HTTPException(404, "Sensei não encontrado.")
    if p.level < s["nivel"]:
        raise HTTPException(403, f"{s['nome']} só aceita alunos a partir do nível {s['nivel']}.")
    if p.energia < payload.sessoes:
        raise HTTPException(400, f"Energia insuficiente: {payload.sessoes} sessão(ões) custam {payload.sessoes} de energia (você tem {p.energia}).")
    custo = s["preco"] * payload.sessoes
    if p.ryos < custo:
        raise HTTPException(400, f"Ryos insuficientes: custa {custo}.")
    attr, grau_antes, ganho = payload.atributo, maestria_grau(p, payload.atributo), 0
    mae = dict(p.maestria or {})
    for _ in range(payload.sessoes):  # o grau pode subir no meio das sessões e já vale para a seguinte
        g = min(MAESTRIA_MAX, mae.get(attr, 0) // MAESTRIA_PTS)
        pts = max(s["ganho"], round(s["ganho"] * (1 + MAESTRIA_BONUS * g)))
        ganho += pts
        mae[attr] = mae.get(attr, 0) + pts
    p.maestria = mae
    p.energia -= payload.sessoes
    p.ryos -= custo
    dar_atributo(p, attr, ganho)
    niveis = apply_exp(p, XP_POR_PONTO * ganho)
    grau = maestria_grau(p, attr)
    prog(p, "dojo", payload.sessoes)
    db.commit()
    msg = f"{s['nome']} treinou você: +{ganho} de {attr} e +{XP_POR_PONTO * ganho} de XP."
    if grau > grau_antes:
        msg += f" Maestria de {attr}: grau {grau}!"
    if niveis:
        msg += " Você subiu de nível!"
    return {"mensagem": msg, "ganho": ganho, "grau": grau, "niveis_ganhos": niveis}


# ───────── Masmorras: 5 andares seguidos, entrada por fragmentos de chave ─────────
# Chefes de mapa soltam fragmentos da chave daquele mapa (60%). Com 3 deles e 5 de energia, o jogador entra na masmorra do mapa:
# 5 andares em sequência (o 5º é o chefe), sem cura automática entre eles. Fugir ou cair encerra a corrida e perde o progresso.
# Cada andar rende XP e ryos como uma luta do mapa; o baú final dá materiais, um equipamento com chance maior de ★ e fama.
MASM_ANDARES, MASM_FRAGS, MASM_ENERGIA, FRAG_CHANCE = 5, 3, 5, 0.6
MASM_DIF = ["normal", "normal", "difícil", "mini-chefe", "chefe"]


def frag_nome(mid: int, vi: int) -> str:
    return f"Fragmento de Chave: {mapa_vila(mid, vi)['nome']}"


def dropar_fragmento(p: Player, L: dict) -> list:
    if not L["boss"] or random.random() >= FRAG_CHANCE:
        return []
    k = f"frag_{L['mapa']}"
    p.materiais = {**(p.materiais or {}), k: (p.materiais or {}).get(k, 0) + 1}
    return [frag_nome(L["mapa"], vila_idx(p))]


def materiais_view(p: Player, vi: int) -> list:
    """Materiais de forja e fragmentos de chave em estoque, para a aba Materiais da mochila."""
    out = []
    for k, q in sorted((p.materiais or {}).items()):
        if q <= 0:
            continue
        if k in MAT_NOME:
            out.append({"id": k, "nome": MAT_NOME[k], "tier": int(k[-1]), "qtd": q})
        elif k.startswith("frag_") and int(k[5:]) in MAPS_BY_ID:
            out.append({"id": k, "nome": frag_nome(int(k[5:]), vi), "tier": None, "qtd": q})
    return out


def _novo_andar(p: Player, mid: int, andar: int) -> None:
    """Abre a luta do andar `andar` da masmorra do mapa `mid`."""
    vi, base = vila_idx(p), MAPS_BY_ID[mid]
    mp, dif = mapa_vila(mid, vi), MASM_DIF[andar - 1]
    lo, hi = base["min_level"], base["max_level"]
    nivel = hi + random.randint(0, 1) if dif == "chefe" else lo + (hi - lo) * andar // MASM_ANDARES
    comum = random.choice([mp["inimigo"]] * 2 + mp.get("extras", []))
    nome = mp["chefe"] if dif == "chefe" else f"{comum} Alfa" if dif == "mini-chefe" else comum
    stats, hp, ck = stats_inimigo(p, nivel, dif)
    iniciar_luta(p, "masmorra", nome, stats, hp, ck, dif == "chefe", mapa=mid, on_mission=False, elemento=elem_mapa(vi),
                 dif=dif, nivel=nivel, andar=andar, total=MASM_ANDARES)


def fim_masmorra(db: Session, p: Player, L: dict, won: bool, res: dict) -> None:
    andar, mp = L["andar"], MAPS_BY_ID[L["mapa"]]
    if not won:
        p.masmorra = None
        res["masmorra"] = {"andar": andar, "total": MASM_ANDARES, "fim": True, "falhou": True}
        return
    mult, over = DIFICULDADES[L["dif"]][2], p.level > mp["max_level"]
    res["exp"] = max(1, int(6 * mp["perigo"] * mult * (0.25 if over else 1)))
    res["ryos"] = int(8 * mp["perigo"] * mult)
    mr, mx = multiplicador_ryos(p), multiplicador_xp(p)  # bônus do VIP
    res["exp"], res["ryos"] = int(res["exp"] * mx), int(res["ryos"] * mr)
    p.ryos += res["ryos"]
    p.fama += 2
    pet_afinidade(db, L)
    fim = andar >= MASM_ANDARES
    if fim:
        qtd, mid = random.randint(6, 10), mat_do_mapa(mp["perigo"])
        p.materiais = {**(p.materiais or {}), mid: (p.materiais or {}).get(mid, 0) + qtd}
        res["extras"].append(f"{qtd}x {MAT_NOME[mid]}")
        if bolsa_livre(p, len(res["drops"])):
            nome, attr, *t = random.choice(mp["itens"])
            res["drops"].append(criar_item(db, p, mp, nome, attr, t[0] if t else mp["tipo"], sortear_estrelas(min(MORAL_MAX, p.moral + 12))))
        else:
            res["extras"].append("o baú tinha um equipamento, mas a mochila estava cheia")
        p.fama += 20
        p.masmorra = None
    else:
        p.masmorra = {"mapa": L["mapa"], "andar": andar}  # andares já vencidos
    res["masmorra"] = {"andar": andar, "total": MASM_ANDARES, "fim": fim, "falhou": False}


@router.get("/api/player/{telegram_id}/masmorras")
def masmorras_info(telegram_id: int, db: Session = Depends(get_db)):
    p = get_player(db, telegram_id)
    vi, m = vila_idx(p), p.masmorra
    return {"andares": MASM_ANDARES, "frags": MASM_FRAGS, "energia": MASM_ENERGIA,
            "ativa": {"mapa": m["mapa"], "nome": mapa_vila(m["mapa"], vi)["nome"], "feitos": m["andar"]} if m else None,
            "mapas": [{"id": x["id"], "nome": mapa_vila(x["id"], vi)["nome"], "min_level": x["min_level"], "max_level": x["max_level"],
                       "qtd": (p.materiais or {}).get(f"frag_{x['id']}", 0), "pode_nivel": p.level >= x["min_level"]} for x in MAPS]}


@router.post("/api/masmorra/entrar")
def masmorra_entrar(payload: ExploreReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    if p.luta:
        raise HTTPException(409, "Você já está em uma luta.")
    if p.masmorra:
        raise HTTPException(409, "Você já está em uma masmorra. Continue ou abandone-a.")
    if payload.map_id not in MAPS_BY_ID:
        raise HTTPException(404, "Mapa não encontrado.")
    mp = MAPS_BY_ID[payload.map_id]
    if p.level < mp["min_level"]:
        raise HTTPException(403, f"Masmorra bloqueada: exige nível {mp['min_level']}.")
    k = f"frag_{payload.map_id}"
    if (p.materiais or {}).get(k, 0) < MASM_FRAGS:
        raise HTTPException(400, f"Faltam fragmentos: precisa de {MASM_FRAGS} ({(p.materiais or {}).get(k, 0)}). Eles caem de chefes deste mapa.")
    if p.energia < MASM_ENERGIA:
        raise HTTPException(400, f"Energia insuficiente: a entrada custa {MASM_ENERGIA}.")
    if p.vida_atual < 0.5 * p.vida_max:
        raise HTTPException(400, "Vida abaixo da metade. Recupere-se antes de entrar: não há cura entre os andares.")
    p.materiais = {**p.materiais, k: p.materiais[k] - MASM_FRAGS}
    p.energia -= MASM_ENERGIA
    p.masmorra = {"mapa": payload.map_id, "andar": 0}
    _novo_andar(p, payload.map_id, 1)
    db.commit()
    return {"luta": luta_view(p)}


@router.post("/api/masmorra/avancar")
def masmorra_avancar(payload: RestReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    if p.luta:
        raise HTTPException(409, "Você já está em uma luta.")
    if not p.masmorra:
        raise HTTPException(409, "Você não está em uma masmorra.")
    if p.vida_atual < 10:
        raise HTTPException(400, "Vida baixa demais para o próximo andar. Use uma poção ou abandone a masmorra.")
    _novo_andar(p, p.masmorra["mapa"], p.masmorra["andar"] + 1)
    db.commit()
    return {"luta": luta_view(p)}


@router.post("/api/masmorra/abandonar")
def masmorra_abandonar(payload: RestReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    if p.luta:
        raise HTTPException(409, "Termine a luta antes.")
    if not p.masmorra:
        raise HTTPException(409, "Você não está em uma masmorra.")
    p.masmorra = None
    db.commit()
    return {"mensagem": "Você deixou a masmorra. Os fragmentos usados na entrada se perderam."}


# ───────── Torre: andares infinitos, sem tempo ─────────
# Cada tentativa gasta 2 de energia e luta contra o próximo andar. Perder não tira nada. Vencer sobe o recorde e rende a recompensa:
# XP, ryos e fama em todo andar; materiais; equipamento a cada 5 andares; chefe e ovo de pet a cada 10.
TORRE_ENERGIA = 2
TORRE_CHEFES = ["Sentinela da Torre", "Carrasco de Ferro", "Dama das Cem Lâminas", "Monge do Vazio", "Imperador de Cinzas",
                "Guardião Celeste", "Arauto do Trovão Negro", "Rei Sem Nome", "Serpente das Eras", "O Último Guardião"]


def torre_dif(f: int) -> str:
    return "chefe" if f % 10 == 0 else "mini-chefe" if f % 5 == 0 else "difícil" if f % 3 == 0 else "normal"


def torre_recompensa(f: int) -> dict:
    """O que o andar `f` dá na primeira vitória (ryos e XP crescem com o quadrado do andar)."""
    return {"exp": 20 * f + 2 * f * f, "ryos": 30 * f + 3 * f * f, "fama": 20 if f % 10 == 0 else 3 if f % 5 == 0 else 1,
            "equip": f % 5 == 0, "ovo": f % 10 == 0, "chefe": f % 10 == 0}


def fim_torre(db: Session, p: Player, L: dict, won: bool, res: dict) -> None:
    f = L["andar"]
    if not won:
        p.torre_sessao = False  # cair expulsa da Torre
        res["torre"] = {"andar": f, "recorde": p.torre_andar or 0, "falhou": True, "boss": L["boss"]}
        return
    rec = torre_recompensa(f)
    p.torre_andar = max(p.torre_andar or 0, f)
    res["exp"], res["ryos"] = rec["exp"], rec["ryos"]
    p.ryos += rec["ryos"]
    p.fama += rec["fama"]
    pet_afinidade(db, L)
    mid = f"mat_{min(5, f // 20 + 1)}"
    qtd = random.randint(3, 5) if rec["chefe"] else random.randint(1, 2)
    p.materiais = {**(p.materiais or {}), mid: (p.materiais or {}).get(mid, 0) + qtd}
    res["extras"].append(f"{qtd}x {MAT_NOME[mid]}")
    if rec["ovo"]:
        oid = list(OVOS)[0 if f < 30 else 1 if f < 60 else 2 if f < 90 else 3]
        p.ovos = {**(p.ovos or {}), oid: (p.ovos or {}).get(oid, 0) + 1}
        res["extras"].append(OVOS[oid][0])
    if rec["equip"]:
        if bolsa_livre(p, len(res["drops"])):
            mp = MAPS_BY_ID[min(15, 1 + f // 7)]
            nome, attr, *t = random.choice(mp["itens"])
            res["drops"].append(criar_item(db, p, mp, nome, attr, t[0] if t else mp["tipo"], sortear_estrelas(min(MORAL_MAX, p.moral + 8 + f // 10))))
        else:
            res["extras"].append("um equipamento, mas a mochila estava cheia")
    res["torre"] = {"andar": f, "recorde": p.torre_andar, "falhou": False, "boss": rec["chefe"]}


@router.get("/api/player/{telegram_id}/torre")
def torre_info(telegram_id: int, db: Session = Depends(get_db)):
    p = get_player(db, telegram_id)
    f = (p.torre_andar or 0) + 1
    top = db.scalars(select(Player).where(Player.torre_andar > 0).order_by(Player.torre_andar.desc(), Player.level.desc()).limit(10)).all()
    return {"sessao": bool(p.torre_sessao), "andar": f, "recorde": p.torre_andar or 0, "energia": TORRE_ENERGIA, "dif": torre_dif(f), "recompensa": torre_recompensa(f),
            "ranking": [{"nome": x.ninja_name, "avatar": x.avatar, "andar": x.torre_andar, "eu": x.id == p.id} for x in top]}


@router.post("/api/torre/sair")
def torre_sair(payload: RestReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    if p.luta:
        raise HTTPException(409, "Termine a luta antes de sair da Torre.")
    p.torre_sessao = False
    db.commit()
    return {"mensagem": "Você saiu da Torre. O hospital voltou a atender."}


@router.post("/api/torre/lutar")
def torre_lutar(payload: RestReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    if p.luta:
        raise HTTPException(409, "Você já está em uma luta.")
    if p.energia < TORRE_ENERGIA:
        raise HTTPException(400, f"Energia insuficiente: cada tentativa custa {TORRE_ENERGIA}.")
    if p.vida_atual < 10:
        raise HTTPException(400, "Vida baixa demais. Descanse antes de subir.")
    f = (p.torre_andar or 0) + 1
    dif = torre_dif(f)
    nivel = 3 + f + (2 if dif == "chefe" else 0)
    nome = TORRE_CHEFES[(f // 10 - 1) % len(TORRE_CHEFES)] if dif == "chefe" else f"Capitão do {f}º Andar" if dif == "mini-chefe" else f"Guardião do {f}º Andar"
    stats, hp, ck = stats_inimigo(p, nivel, dif)
    p.energia -= TORRE_ENERGIA
    p.torre_sessao = True  # dentro da Torre não há hospital nem descanso: só itens da bolsa
    iniciar_luta(p, "torre", nome, stats, hp, ck, dif == "chefe", mapa=None, on_mission=False, elemento=random.choice(list(ELEMENTOS)),
                 dif=dif, nivel=nivel, andar=f)
    db.commit()
    return {"luta": luta_view(p)}


# ───────── Arena: duelos ranqueados, troféus, moedas e loja ─────────
# Cada duelo custa 2 de energia e não tem fuga. Contra jogadores, os troféus seguem uma fórmula de Elo (só o desafiante muda; o
# defensor luta com os atributos reais e não perde nada). Os Gladiadores (NPC) garantem adversário mesmo com poucos jogadores,
# mas só dão troféus até um teto. As moedas da Arena compram itens na loja da Arena.
ARENA_ENERGIA, ARENA_VS_DIA, ARENA_LUTAS_DIA = 2, 3, 12  # custo por duelo; lutas por dia contra o mesmo jogador; lutas por dia no total
ARENA_LIGAS = [(0, "Bronze"), (100, "Prata"), (300, "Ouro"), (600, "Platina"), (1000, "Diamante"), (1500, "Lenda")]
# id: (nome, custo em moedas, limite por dia ou None, tipo, argumento, quantidade)
ARENA_LOJA = {"energia": ("Recarga de energia", 25, 2, "energia", None, 25),
              "elixir": ("3x Elixir de Vida", 25, None, "pocao", "vida_m", 3),
              "mat3": ("5x Cristal de Chakra", 40, None, "mat", "mat_3", 5),
              "mat4": ("5x Essência Elemental", 90, None, "mat", "mat_4", 5),
              "mat5": ("3x Fragmento Celestial", 200, None, "mat", "mat_5", 3),
              "ovo_incomum": ("Ovo Incomum", 60, None, "ovo", "ovo_incomum", 1),
              "ovo_raro": ("Ovo Raro", 160, None, "ovo", "ovo_raro", 1)}


class ArenaReq(BaseModel):
    telegram_id: int = Field(gt=0)
    alvo: Optional[str] = Field(default=None, min_length=3, max_length=32)  # jogador ...


class ArenaBuyReq(BaseModel):
    telegram_id: int = Field(gt=0)
    item_id: str = Field(max_length=32)


def arena_liga(t: int) -> str:
    return [n for lim, n in ARENA_LIGAS if t >= lim][-1]


def _arena_reg(p: Player) -> dict:
    """Registro da Arena, com os contadores diários zerados quando o dia (UTC) vira."""
    a, hoje = copy.deepcopy(p.arena or {}), datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if a.get("dia") != hoje:
        a["dia"], a["vs"], a["loja"], a["n"] = hoje, {}, {}, 0
    a.setdefault("n", 0)
    for k in ("v", "d", "seq", "melhor"):
        a.setdefault(k, 0)
    a.setdefault("vs", {})
    a.setdefault("loja", {})
    return a


# Rankings da Arena: diário, semanal e mensal contam os troféus ganhos (líquidos) no período. A semana que passou premia o top.
def _rk_keys() -> dict:
    now = datetime.now(timezone.utc)
    y, w, _ = now.isocalendar()
    py, pw, _ = (now - timedelta(days=7)).isocalendar()
    return {"d": now.strftime("%Y-%m-%d"), "s": f"{y}-W{w:02d}", "m": now.strftime("%Y-%m"), "sp": f"{py}-W{pw:02d}"}


def _rk_add(a: dict, delta: int) -> None:
    ks, rk = _rk_keys(), a.setdefault("rk", {})
    for per in ("d", "s", "m"):
        r = rk.get(per)
        if r and r["k"] != ks[per]:
            if per == "s":
                rk["sp"] = r  # guarda a semana que acabou para o prêmio semanal
            r = None
        r = r or {"k": ks[per], "p": 0}
        r["p"] += delta
        rk[per] = r


def _rk_pts(a: Optional[dict], per: str) -> int:
    ks, rk = _rk_keys(), (a or {}).get("rk") or {}
    if per == "sp":
        for r in (rk.get("sp"), rk.get("s")):
            if r and r["k"] == ks["sp"]:
                return r["p"]
        return 0
    r = rk.get(per)
    return r["p"] if r and r["k"] == ks[per] else 0


def _rk_lista(rows, per: str) -> list:
    lista = [(_rk_pts(r.arena, per), r) for r in rows]
    return sorted([x for x in lista if x[0] > 0], key=lambda x: (-x[0], x[1].ninja_name))


def _rk_view(lista: list, meu_id: int, n: int = 10) -> dict:
    pos = next((i + 1 for i, (_, r) in enumerate(lista) if r.id == meu_id), None)
    return {"top": [{"nome": r.ninja_name, "avatar": r.avatar, "nivel": r.level, "pts": pts, "eu": r.id == meu_id} for pts, r in lista[:n]],
            "pos": pos, "pts": next((pts for pts, r in lista if r.id == meu_id), 0)}


ARENA_SEMANAL = [(1, 300, "ovo_raro"), (2, 200, "ovo_incomum"), (3, 120, "ovo_incomum"), (10, 60, None), (10 ** 9, 15, None)]


def _premio_semanal(pos: Optional[int]) -> Optional[dict]:
    if pos is None:
        return None
    for lim, moedas, ovo in ARENA_SEMANAL:
        if pos <= lim:
            return {"moedas": moedas, "ovo": ovo}


def _arena_rows(db: Session):
    return db.execute(select(Player.id, Player.ninja_name, Player.avatar, Player.level, Player.arena)).all()


# Temporadas: ciclos de 14 dias (a primeira começa em 19/10/2026, UTC). Na primeira visita de uma nova temporada, quem jogou na anterior
# ganha o prêmio da liga em que terminou (a coletar) e os troféus caem pela metade. O ranking só mostra quem pontuou na temporada atual.
ARENA_INICIO, ARENA_DIAS = date(2026, 10, 19), 14
ARENA_PREMIOS = {"Bronze": (20, None), "Prata": (60, None), "Ouro": (150, None), "Platina": (300, "ovo_raro"),
                 "Diamante": (600, "ovo_raro"), "Lenda": (1000, "ovo_lendario")}


def temporada_atual() -> int:
    return max(0, (datetime.now(timezone.utc).date() - ARENA_INICIO).days // ARENA_DIAS)


def temporada_dias_restantes() -> int:
    return (ARENA_INICIO + timedelta(days=(temporada_atual() + 1) * ARENA_DIAS) - datetime.now(timezone.utc).date()).days


def arena_virar(p: Player) -> bool:
    """Se a temporada mudou desde a última visita, concede o prêmio pendente e reduz os troféus. Retorna True quando algo mudou."""
    atual = temporada_atual()
    if (p.arena_temp or 0) == atual:
        return False
    a, t = _arena_reg(p), p.trofeus or 0
    if a["v"] + a["d"] > 0:  # só quem jogou na temporada anterior ganha prêmio
        liga = arena_liga(t)
        moedas, ovo = ARENA_PREMIOS[liga]
        a["premio"] = {"temporada": (p.arena_temp or 0) + 1, "liga": liga, "trofeus": t, "moedas": moedas, "ovo": ovo}
        a["hist"] = ([{"temporada": (p.arena_temp or 0) + 1, "liga": liga, "trofeus": t}] + a.get("hist", []))[:5]
    p.trofeus = t // 2
    a["v"], a["d"], a["seq"], a["melhor"] = 0, 0, 0, p.trofeus
    p.arena, p.arena_temp = a, atual
    return True


def fim_arena(db: Session, p: Player, L: dict, won: bool, res: dict) -> None:
    arena_virar(p)  # a luta pode ter começado em outra temporada
    a, t = _arena_reg(p), p.trofeus or 0
    # Elo: vencer um jogador de mais troféus rende mais, perder para um de menos custa mais
    esperado = 1 / (1 + 10 ** ((L.get("alvo_trof", 0) - t) / 200))
    delta = max(5, round(30 * (1 - esperado))) if won else -max(3, round(20 * esperado))
    moedas = 4 + t // 250
    novo = max(0, t + delta)
    delta = novo - t
    p.trofeus = novo
    if won:
        a["v"], a["seq"] = a["v"] + 1, a["seq"] + 1
        ganho = moedas + min(5, a["seq"] - 1)  # bônus de sequência: +1 por vitória seguida, até +5
        res["exp"] = 4 + p.level // 3
        p.fama += 3
    else:
        a["d"], a["seq"], ganho = a["d"] + 1, 0, 1
        res["exp"] = 1
    a["melhor"] = max(a["melhor"], novo)
    _rk_add(a, delta)
    p.moedas_arena = (p.moedas_arena or 0) + ganho
    p.arena = a
    res["arena"] = {"delta": delta, "trofeus": novo, "liga": arena_liga(novo), "moedas": ganho, "seq": a["seq"], "npc": False}


@router.get("/api/player/{telegram_id}/arena")
def arena_info(telegram_id: int, db: Session = Depends(get_db)):
    p = get_player(db, telegram_id)
    if arena_virar(p):
        db.commit()
    a, t = _arena_reg(p), p.trofeus or 0
    vs = a["vs"]
    cand = db.scalars(select(Player).where(Player.id != p.id).order_by(func.abs(Player.trofeus - t), func.abs(Player.level - p.level)).limit(5)).all()
    top = db.scalars(select(Player).where(Player.trofeus > 0, Player.arena_temp == temporada_atual()).order_by(Player.trofeus.desc(), Player.level.desc()).limit(10)).all()
    rows = _arena_rows(db)
    rks = {per: _rk_view(_rk_lista(rows, per), p.id) for per in ("d", "s", "m")}
    sp = _rk_view(_rk_lista(rows, "sp"), p.id)
    chave = _rk_keys()["sp"]
    prem = _premio_semanal(sp["pos"])
    semanal = {"chave": chave, "pos": sp["pos"], "pts": sp["pts"], "dias": 7 - datetime.now(timezone.utc).weekday(),
               "premio": {"moedas": prem["moedas"], "ovo": OVOS[prem["ovo"]][0] if prem["ovo"] else None} if prem else None,
               "pronto": bool(prem) and a.get("sem_col") != chave, "tabela": [{"ate": l if l < 10 ** 9 else None, "moedas": m, "ovo": OVOS[o][0] if o else None} for l, m, o in ARENA_SEMANAL]}
    return {"trofeus": t, "liga": arena_liga(t), "moedas": p.moedas_arena or 0, "v": a["v"], "d": a["d"], "seq": a["seq"], "melhor": a["melhor"],
            "temporada": {"n": temporada_atual() + 1, "dias": temporada_dias_restantes(), "premio": a.get("premio"), "hist": a.get("hist", []),
                          "premios": [{"liga": n, "moedas": v[0], "ovo": OVOS[v[1]][0] if v[1] else None} for n, v in ARENA_PREMIOS.items()]},
            "energia": ARENA_ENERGIA, "vs_dia": ARENA_VS_DIA, "lutas_hoje": a["n"], "lutas_dia": ARENA_LUTAS_DIA,
            "oponentes": [{"nome": x.ninja_name, "avatar": x.avatar, "nivel": x.level, "rank": rank_titulo(x), "trofeus": x.trofeus or 0,
                           "liga": arena_liga(x.trofeus or 0), "hoje": vs.get(x.ninja_name, 0)} for x in cand],
            "loja": [{"id": k, "nome": v[0], "custo": v[1], "restam": None if v[2] is None else max(0, v[2] - a["loja"].get(k, 0))} for k, v in ARENA_LOJA.items()],
            "ranking": [{"nome": x.ninja_name, "avatar": x.avatar, "trofeus": x.trofeus, "liga": arena_liga(x.trofeus), "eu": x.id == p.id} for x in top],
            "rankings": rks, "semanal": semanal}


@router.post("/api/arena/lutar")
def arena_lutar(payload: ArenaReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    if payload.alvo is None:
        raise HTTPException(400, "Escolha um jogador para duelar.")
    arena_virar(p)
    if p.luta:
        raise HTTPException(409, "Você já está em uma luta.")
    if p.masmorra:
        raise HTTPException(409, "Termine ou abandone a masmorra antes de entrar na Arena.")
    if p.energia < ARENA_ENERGIA:
        raise HTTPException(400, f"Duelos da Arena custam {ARENA_ENERGIA} de energia.")
    if p.vida_atual < 0.3 * p.vida_max:
        raise HTTPException(400, "Vida baixa demais para a Arena (mínimo 30%). Descanse antes.")
    a = _arena_reg(p)
    if a["n"] >= ARENA_LUTAS_DIA:
        raise HTTPException(400, f"Você já fez as {ARENA_LUTAS_DIA} lutas da Arena de hoje. Volte amanhã (meia-noite UTC).")
    t = db.scalar(select(Player).where(Player.ninja_name == payload.alvo))
    if t is None:
        raise HTTPException(404, "Jogador não encontrado.")
    if t.id == p.id:
        raise HTTPException(400, "Você não pode duelar consigo mesmo.")
    if a["vs"].get(t.ninja_name, 0) >= ARENA_VS_DIA:
        raise HTTPException(400, f"Você já enfrentou {t.ninja_name} {ARENA_VS_DIA} vezes hoje. Escolha outro adversário.")
    a["vs"][t.ninja_name] = a["vs"].get(t.ninja_name, 0) + 1
    a["n"] += 1
    nome, stats, hp, ck = t.ninja_name, {**{k: int(v) for k, v in effective_stats(t).items()}, "_classe": atributos5.classe_efetiva(t)}, t.vida_max, t.chakra_max
    extra, elem, boss = {"npc": None, "alvo_trof": t.trofeus or 0, "nivel": t.level}, t.elemento, False
    p.arena = a
    p.energia -= ARENA_ENERGIA
    iniciar_luta(p, "arena", nome, stats, hp, ck, boss, elemento=elem, mapa=None, on_mission=False, **extra)
    db.commit()
    return {"luta": luta_view(p)}


@router.post("/api/arena/premio")
def arena_premio(payload: RestReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    a = _arena_reg(p)
    pr = a.pop("premio", None)
    if not pr:
        raise HTTPException(409, "Você não tem prêmio de temporada para coletar.")
    p.moedas_arena = (p.moedas_arena or 0) + pr["moedas"]
    txt = f"{pr['moedas']} moedas da Arena"
    if pr["ovo"]:
        p.ovos = {**(p.ovos or {}), pr["ovo"]: (p.ovos or {}).get(pr["ovo"], 0) + 1}
        txt += f" e {OVOS[pr['ovo']][0]}"
    p.arena = a
    db.commit()
    return {"mensagem": f"Prêmio da temporada {pr['temporada']} ({pr['liga']}): {txt}."}


@router.post("/api/arena/semanal")
def arena_semanal(payload: RestReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    a, chave = _arena_reg(p), _rk_keys()["sp"]
    if a.get("sem_col") == chave:
        raise HTTPException(409, "Você já coletou o prêmio semanal.")
    sp = _rk_view(_rk_lista(_arena_rows(db), "sp"), p.id)
    prem = _premio_semanal(sp["pos"])
    if not prem:
        raise HTTPException(409, "Você não pontuou na semana passada.")
    p.moedas_arena = (p.moedas_arena or 0) + prem["moedas"]
    txt = f"{prem['moedas']} moedas da Arena"
    if prem["ovo"]:
        p.ovos = {**(p.ovos or {}), prem["ovo"]: (p.ovos or {}).get(prem["ovo"], 0) + 1}
        txt += f" e {OVOS[prem['ovo']][0]}"
    a["sem_col"] = chave
    p.arena = a
    db.commit()
    return {"mensagem": f"Prêmio semanal da Arena ({sp['pos']}º lugar): {txt}."}


@router.post("/api/arena/comprar")
def arena_comprar(payload: ArenaBuyReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    it = ARENA_LOJA.get(payload.item_id)
    if it is None:
        raise HTTPException(404, "Item não encontrado.")
    nome, custo, lim, tipo, arg, qtd = it
    a = _arena_reg(p)
    if lim is not None and a["loja"].get(payload.item_id, 0) >= lim:
        raise HTTPException(400, f"Limite diário atingido para {nome}.")
    if (p.moedas_arena or 0) < custo:
        raise HTTPException(400, f"Moedas da Arena insuficientes: {nome} custa {custo}.")
    if tipo == "energia":
        if p.energia >= 25:
            raise HTTPException(400, "Sua energia já está cheia.")
        p.energia = 25
    elif tipo == "pocao":
        p.pocoes = {**(p.pocoes or {}), arg: (p.pocoes or {}).get(arg, 0) + qtd}
    elif tipo == "mat":
        p.materiais = {**(p.materiais or {}), arg: (p.materiais or {}).get(arg, 0) + qtd}
    else:
        p.ovos = {**(p.ovos or {}), arg: (p.ovos or {}).get(arg, 0) + qtd}
    p.moedas_arena -= custo
    if lim is not None:
        a["loja"][payload.item_id] = a["loja"].get(payload.item_id, 0) + 1
    p.arena = a
    db.commit()
    return {"mensagem": f"Você comprou {nome}.", "moedas": p.moedas_arena}


# ───────── Excluir conta ─────────
class ExcluirReq(BaseModel):
    telegram_id: int = Field(gt=0)
    confirmar: str = Field(min_length=1, max_length=64)  # o jogador digita o nome do ninja


@router.post("/api/conta/excluir")
def conta_excluir(payload: ExcluirReq, db: Session = Depends(get_db)):
    """Apaga o personagem de forma definitiva. Exige digitar o nome do ninja. Quem lidera uma guilda passa o comando adiante."""
    from models import PlayerMission
    p = get_player(db, payload.telegram_id, lock=True)
    if payload.confirmar.strip() != p.ninja_name:
        raise HTTPException(400, "Digite exatamente o nome do seu ninja para confirmar.")
    if p.luta or p.masmorra:
        raise HTTPException(409, "Termine a luta ou a masmorra antes de excluir a conta.")
    pid = p.id
    if p.guilda_id:
        guild_leave(RestReq(telegram_id=payload.telegram_id), db)
        p = get_player(db, payload.telegram_id, lock=True)
    for v in db.scalars(select(Village).where((Village.leader_kage_id == pid) | (Village.challenger_id == pid))).all():
        if v.leader_kage_id == pid:
            v.leader_kage_id = None
        if v.challenger_id == pid:
            v.challenger_id, v.challenger_since = None, None
    db.flush()
    for modelo, col in ((Pet, Pet.player_id), (MarketListing, MarketListing.seller_id), (PlayerMission, PlayerMission.player_id)):
        for x in db.scalars(select(modelo).where(col == pid)).all():
            db.delete(x)
    db.delete(p)
    db.commit()
    return {"mensagem": "Conta excluída."}


# ───────── Guilda completa: cargos, mural, perk de nível e chefe cooperativo ─────────
# Cargos: líder (tudo), vice (até 2: expulsa membros e edita o mural) e membro. O nível da guilda libera um bônus de XP e ryos
# nas caçadas. O chefe da guilda tem um HP compartilhado: cada membro luta contra ele até 2 vezes por dia e o dano entra no total;
# quando cai, todo mundo que bateu recebe uma recompensa proporcional ao dano, para coletar na tela da guilda.
GUILD_VICES, GUILD_CHEFE_ENERGIA, GUILD_CHEFE_TENT = 2, 3, 2
GCHEFES = ["Colosso de Pedra Antiga", "Serpente das Névoas", "Samurai Sem Rosto", "Lorde das Sombras", "Dragão do Vale Esquecido",
           "Imperatriz de Gelo", "Titã de Ferro Negro", "O Devorador de Chakra"]


def cargo_guilda(g: Guild, pid: int) -> str:
    return "lider" if g.leader_id == pid else "vice" if pid in (g.dados or {}).get("vices", []) else "membro"


GUILD_MEMBROS_MAX = 4  # regra do jogo: no máximo 4 membros por guilda (guildas que já passam disso só deixam de aceitar entradas)


def guild_limite(g: Guild) -> int:
    return GUILD_MEMBROS_MAX


# Tesouraria: buffs comprados com o cofre da guilda. id: (nome, custo em ryos do cofre, % de XP e ryos, horas)
BUFFS_GUILDA = {"bencao": ("Bênção da guilda", 4000, 10, 24), "bencao2": ("Grande Bênção da guilda", 9000, 20, 24)}


def bonus_guilda(p: Player) -> int:
    """Perk de nível da guilda (+1% de XP e ryos por nível, até +15%) mais os buffs ativos comprados na tesouraria."""
    if not (p.guilda_id and p.guild):
        return 0
    total = min(15, p.guild.level)
    agora = datetime.now(timezone.utc)
    for bid, ate in ((p.guild.dados or {}).get("buffs") or {}).items():
        if bid in BUFFS_GUILDA and datetime.fromisoformat(ate) > agora:
            total += BUFFS_GUILDA[bid][2]
    return total


def _dados_set(g: Guild, **kw) -> None:
    g.dados = {**(g.dados or {}), **kw}  # reatribui o dict inteiro para o SQLAlchemy perceber a mudança


def _hoje() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _chefe_hpmax(g: Guild, n: int) -> int:
    return int((3000 + 1500 * g.level) * (1 + 0.1 * (n - 1)))


def _chefe(g: Guild) -> dict:
    c = (g.dados or {}).get("chefe")
    if c:
        return {**c, "dano": dict(c.get("dano", {}))}
    hp = _chefe_hpmax(g, 1)
    return {"n": 1, "nome": GCHEFES[0], "hp": hp, "hp_max": hp, "dano": {}}


def _tentativas(g: Guild, p: Player) -> int:
    t = (g.dados or {}).get("tent") or {}
    return t.get("n", {}).get(str(p.id), 0) if t.get("dia") == _hoje() else 0


def _pendente(g: Guild, p: Player):
    return ((g.dados or {}).get("pend") or {}).get(str(p.id))


def chefe_view(g: Guild, p: Player) -> dict:
    c = _chefe(g)
    return {"nome": c["nome"], "n": c["n"], "hp": c["hp"], "hp_max": c["hp_max"], "meu_dano": c["dano"].get(str(p.id), 0),
            "restam": max(0, GUILD_CHEFE_TENT - _tentativas(g, p)), "energia": GUILD_CHEFE_ENERGIA}


def fim_gchefe(db: Session, p: Player, L: dict, won: bool, res: dict) -> None:
    res["exp"] = 4 + p.level // 2 if won else 2
    g = db.scalar(select(Guild).where(Guild.id == L["gid"]).with_for_update())
    c = _chefe(g) if g else None
    dealt = L["hp_max"] if won else max(0, L["hp_max"] - max(0, L["hp"]))
    if g is None or p.guilda_id != g.id or c["n"] != L["n"]:  # saiu da guilda ou outro membro derrubou esse chefe no meio da luta
        res["gchefe"] = {"dealt": 0, "sem_credito": True, "nome": L["nome"]}
        return
    c["hp"] = max(0, c["hp"] - dealt)
    c["dano"][str(p.id)] = c["dano"].get(str(p.id), 0) + dealt
    morto, pend = c["hp"] <= 0, dict((g.dados or {}).get("pend") or {})
    if morto:
        total, n = sum(c["dano"].values()) or 1, c["n"]
        mid = f"mat_{min(5, 1 + g.level // 3)}"
        for pid, d in c["dano"].items():
            share = d / total
            old = pend.get(pid) or {"ryos": 0, "fama": 0, "mats": {}}
            mats = dict(old["mats"])
            mats[mid] = mats.get(mid, 0) + 2 + int(4 * share)
            pend[pid] = {"ryos": old["ryos"] + int(200 * g.level * n * (0.5 + share)), "fama": old["fama"] + 5, "mats": mats}
        hp = _chefe_hpmax(g, n + 1)
        nome_morto, c = c["nome"], {"n": n + 1, "nome": GCHEFES[n % len(GCHEFES)], "hp": hp, "hp_max": hp, "dano": {}}
    else:
        nome_morto = None
    _dados_set(g, chefe=c, pend=pend)
    res["gchefe"] = {"dealt": dealt, "hp": c["hp"], "hp_max": c["hp_max"], "morto": morto, "nome": nome_morto or c["nome"]}


class MuralReq(BaseModel):
    telegram_id: int = Field(gt=0)
    texto: str = Field(max_length=200)


class CargoReq(BaseModel):
    telegram_id: int = Field(gt=0)
    alvo: str = Field(min_length=3, max_length=32)
    acao: Literal["promover", "rebaixar", "expulsar", "lider"]


@router.post("/api/guild/mural")
def guild_mural(payload: MuralReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    if not p.guilda_id:
        raise HTTPException(409, "Você não está em uma guilda.")
    if cargo_guilda(p.guild, p.id) == "membro":
        raise HTTPException(403, "Só o líder e os vices editam o mural.")
    _dados_set(p.guild, mural=payload.texto.strip())
    db.commit()
    return {"mensagem": "Mural atualizado."}


@router.post("/api/guild/cargo")
def guild_cargo(payload: CargoReq, db: Session = Depends(get_db)):
    """Promove a vice, rebaixa, expulsa ou passa a liderança. Só o líder promove, rebaixa e transfere; vices expulsam membros."""
    p = get_player(db, payload.telegram_id, lock=True)
    if not p.guilda_id:
        raise HTTPException(409, "Você não está em uma guilda.")
    g, meu = p.guild, cargo_guilda(p.guild, p.id)
    t = db.scalar(select(Player).where(Player.ninja_name == payload.alvo, Player.guilda_id == g.id))
    if t is None:
        raise HTTPException(404, "Esse jogador não está na sua guilda.")
    if t.id == p.id:
        raise HTTPException(400, "Você não pode fazer isso consigo mesmo.")
    alvo_cargo, vices = cargo_guilda(g, t.id), list((g.dados or {}).get("vices", []))
    if payload.acao == "expulsar":
        if meu == "membro" or alvo_cargo == "lider" or (meu == "vice" and alvo_cargo != "membro"):
            raise HTTPException(403, "Você não tem autoridade para expulsar esse jogador.")
        t.guilda_id = None
        msg = f"{t.ninja_name} foi expulso da guilda."
        pend = dict((g.dados or {}).get("pend") or {})
        pend.pop(str(t.id), None)  # quem sai perde a recompensa pendente
        _dados_set(g, vices=[v for v in vices if v != t.id], pend=pend)
    else:
        if meu != "lider":
            raise HTTPException(403, "Só o líder faz isso.")
        if payload.acao == "promover":
            if alvo_cargo != "membro":
                raise HTTPException(409, "Esse jogador já tem cargo.")
            if len(vices) >= GUILD_VICES:
                raise HTTPException(409, f"A guilda já tem {GUILD_VICES} vices.")
            _dados_set(g, vices=vices + [t.id])
            msg = f"{t.ninja_name} agora é vice."
        elif payload.acao == "rebaixar":
            if alvo_cargo != "vice":
                raise HTTPException(409, "Esse jogador não é vice.")
            _dados_set(g, vices=[v for v in vices if v != t.id])
            msg = f"{t.ninja_name} voltou a ser membro."
        else:  # lider: o antigo líder segue como membro
            g.leader_id = t.id
            _dados_set(g, vices=[v for v in vices if v != t.id])
            msg = f"{t.ninja_name} é o novo líder da guilda."
    db.commit()
    return {"mensagem": msg}


@router.post("/api/guild/chefe/lutar")
def guild_chefe_lutar(payload: RestReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    if not p.guilda_id:
        raise HTTPException(409, "Você não está em uma guilda.")
    if p.luta:
        raise HTTPException(409, "Você já está em uma luta.")
    if p.masmorra:
        raise HTTPException(409, "Termine ou abandone a masmorra antes.")
    if datetime.now(timezone.utc).weekday() not in (1, 2, 3, 4):
        raise HTTPException(409, "O chefe da guilda aparece de terça a sexta (horário UTC). Volte nesses dias.")
    g = db.scalar(select(Guild).where(Guild.id == p.guilda_id).with_for_update())
    if _tentativas(g, p) >= GUILD_CHEFE_TENT:
        raise HTTPException(400, f"Você já usou as {GUILD_CHEFE_TENT} tentativas de hoje contra o chefe da guilda.")
    if p.energia < GUILD_CHEFE_ENERGIA:
        raise HTTPException(400, f"Energia insuficiente: cada tentativa custa {GUILD_CHEFE_ENERGIA}.")
    if p.vida_atual < 0.3 * p.vida_max:
        raise HTTPException(400, "Vida baixa demais (mínimo 30%). Descanse antes.")
    c = _chefe(g)
    t = dict((g.dados or {}).get("tent") or {})
    if t.get("dia") != _hoje():
        t = {"dia": _hoje(), "n": {}}
    t["n"] = {**t["n"], str(p.id): t["n"].get(str(p.id), 0) + 1}
    _dados_set(g, chefe=c, tent=t)
    p.energia -= GUILD_CHEFE_ENERGIA
    stats, hp, ck = stats_inimigo(p, p.level + 2, "chefe")
    iniciar_luta(p, "gchefe", c["nome"], stats, hp, ck, True, elemento=random.choice(list(ELEMENTOS)), mapa=None, on_mission=False,
                 dif="chefe", nivel=p.level + 2, gid=g.id, n=c["n"])
    db.commit()
    return {"luta": luta_view(p)}


@router.post("/api/guild/chefe/coletar")
def guild_chefe_coletar(payload: RestReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    if not p.guilda_id:
        raise HTTPException(409, "Você não está em uma guilda.")
    g = db.scalar(select(Guild).where(Guild.id == p.guilda_id).with_for_update())
    pend = dict((g.dados or {}).get("pend") or {})
    r = pend.pop(str(p.id), None)
    if not r:
        raise HTTPException(409, "Você não tem recompensa para coletar.")
    p.ryos += r["ryos"]
    p.fama += r["fama"]
    p.materiais = {**(p.materiais or {}), **{k: (p.materiais or {}).get(k, 0) + q for k, q in r["mats"].items()}}
    _dados_set(g, pend=pend)
    db.commit()
    mats = ", ".join(f"{q}x {MAT_NOME[k]}" for k, q in r["mats"].items())
    return {"mensagem": f"Recompensa coletada: {fmt_num(r['ryos'])} ryos, {r['fama']} de fama e {mats}."}


def fmt_num(n: int) -> str:
    return f"{n:,}".replace(",", ".")
