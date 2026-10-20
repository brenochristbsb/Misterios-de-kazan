"""
game_extra.py — Mapas com batalha PvE, drops, inventário, invocações, clãs e reputação.

Este módulo é registrado no final do api.py (app.include_router).
Mantenha api.py, models.py e game_extra.py na mesma pasta.
"""

import copy
import random
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from clas_dados import CLANS
from vilas_dados import VILAS
from jutsus_dados import BASICO, ELEMENTOS, JUTSUS, MARCOS, VANTAGEM
from sqlalchemy import func, select
from sqlalchemy.orm import Session, object_session

from api import ATTRS, NO_CLAN_NAME, active_summon_link, apply_exp, as_utc, effective_stats, get_db, get_player
from models import Clan, Guild, InventoryItem, ItemType, MarketListing, NinjaRank, Pet, Player, PlayerSummon, Summon, Village

router = APIRouter()

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
MAPS_BY_ID = {m["id"]: m for m in MAPS}
vila_idx = lambda p: (p.village_id - 1) % len(VILAS)  # as vilas são semeadas na ordem de vilas_dados.py


def mapa_vila(mid: int, vi: int) -> dict:
    """Mapa do nível `mid` com nome, inimigo e chefe da vila: a dificuldade é igual, só a ambientação muda."""
    nome, inimigo, chefe = VILAS[vi]["mapas"][mid - 1]
    return {**MAPS_BY_ID[mid], "nome": nome, "inimigo": inimigo, "chefe": chefe}

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


def golpe(att: dict, dfn: dict, acao: str, poder: float = 1.0, mod: float = 1.0, crit_extra: float = 0.0):
    """Retorna (dano, crítico, atordoa). Dano 0 = esquivou. `poder` é o multiplicador do jutsu e `mod` o elemental."""
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


def _me(p: Player, L: dict) -> dict:
    """Atributos do jogador na luta: equipamentos, clã, invocação e o bônus do pet, se ele ativou."""
    s = effective_stats(p)
    if L.get("pet"):
        s[L["pet"]["attr"]] += L["pet"]["n"]
    return s


def iniciar_luta(p: Player, tipo: str, nome: str, stats: dict, hp: int, chakra: int, boss: bool = False, **ctx) -> None:
    L = {"tipo": tipo, "nome": nome, "boss": boss, "stats": stats, "hp": hp, "hp_max": hp, "chakra": chakra,
         "turno": 0, "log": [f"{nome} aparece diante de você."], "defendendo": False, "atordoado": False,
         "me_atordoado": False, "pet": None, "pet_id": None, "meu_elem": p.elemento,
         "elemento": random.choice(list(ELEMENTOS)), **ctx}
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
    if stats["agilidade"] > me["agilidade"] or (stats["agilidade"] == me["agilidade"] and random.random() < 0.5):  # iniciativa: o mais ágil começa; empate no sorteio
        L["log"].append(f"{nome} é mais rápido e ataca primeiro.")
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
                       if q > 0 and k in POCOES and POCOES[k][1] in ("vida", "chakra")]}


def fim_luta(db: Session, p: Player, L: dict, won: bool) -> dict:
    """Aplica recompensas (PvE: XP, ryos, drops, missão, fama; duelo: XP e fama) e encerra a luta."""
    p.luta = None
    p.vida_atual = max(1, p.vida_atual)
    res = {"vitoria": won, "chefe": L["boss"], "inimigo": L["nome"], "log": L["log"][-8:], "exp": 5, "ryos": 0,
           "drops": [], "novo_pet": None, "niveis_ganhos": 0, "xp_reduzido": False, "missao": None}
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
    else:
        mp = MAPS_BY_ID[L["mapa"]]
        mult = 2 if L["boss"] else 1
        over = p.level > mp["max_level"]
        res["xp_reduzido"] = over
        if won:
            res["exp"] = max(1, int(6 * mp["perigo"] * mult * (0.25 if over else 1)))
            res["ryos"] = 8 * mp["perigo"] * mult
            p.ryos += res["ryos"]
            p.fama += 5 if L["boss"] else 1
            clan_hooks(db, p, L)
            pet_afinidade(db, L)
            for nome, attr in mp["itens"]:  # drops temáticos: cada mapa solta só o tipo dele
                if random.random() < 0.25 * mult and bolsa_livre(p, len(res["drops"])):
                    db.add(InventoryItem(player_id=p.id, item_name=nome, item_type=ItemType(mp["tipo"]),
                                         status_bonus={attr: 2 * mp["perigo"]}))
                    res["drops"].append(nome)
            if L["on_mission"]:  # coleta: 70% por vitória; caçada: 1 por vitória; procurado: só ao vencer o chefe
                mis = mission_for(p.journey + 1, vila_idx(p))
                gain = {"coleta": int(random.random() < 0.7), "caca": 1, "busca": int(L["boss"])}[mis["tipo"]]
                p.journey_prog = min(mis["qtd"], p.journey_prog + gain)
                res["missao"] = {"progresso": p.journey_prog, "qtd": mis["qtd"]}
    antes = len(p.elementos_extra or [])
    res["niveis_ganhos"] = apply_exp(p, res["exp"])
    if len(p.elementos_extra or []) > antes:
        res["elemento_novo"] = p.elementos_extra[-1]
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
    boss = random.random() < (0.5 if on_mission and mis["tipo"] == "busca" else 0.15)
    power = int((2 * mp["perigo"] + 3) * (1.25 if boss else 1))
    player.energia -= 1
    iniciar_luta(player, "pve", mp["chefe"] if boss else mp["inimigo"], {a: power for a in ATTRS},
                 int((60 + 30 * mp["perigo"]) * (1.4 if boss else 1)), 60 + 20 * mp["perigo"], boss,
                 mapa=mp["id"], on_mission=on_mission, elemento=elem_mapa(vila_idx(player)))
    db.commit()
    return {"luta": luta_view(player)}


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
    iniciar_luta(p, "pvp", t.ninja_name, {k: int(v) for k, v in effective_stats(t).items()}, t.vida_max, t.chakra_max, elemento=t.elemento)
    db.commit()
    return {"luta": luta_view(p)}


@router.post("/api/luta/agir")
def luta_agir(payload: AgirReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    if not p.luta:
        raise HTTPException(409, "Você não está em luta.")
    L, acao = copy.deepcopy(p.luta), payload.acao
    me = _me(p, L)
    L["turno"] += 1
    if L["me_atordoado"]:
        L["me_atordoado"] = False
        L["log"].append("Você está atordoado e perde o turno.")
    elif acao == "fugir":
        if random.random() < 0.3 + 0.5 * me["agilidade"] / (me["agilidade"] + L["stats"]["agilidade"]):
            p.luta = None
            db.commit()
            return {"luta": None, "resultado": {"vitoria": False, "fuga": True, "chefe": L["boss"], "inimigo": L["nome"],
                    "log": L["log"][-4:] + ["Você fugiu."], "exp": 0, "ryos": 0, "drops": [], "novo_pet": None,
                    "niveis_ganhos": 0, "xp_reduzido": False, "missao": None}}
        L["log"].append("Você não conseguiu fugir.")
    elif acao == "pocao":  # usar poção gasta o turno
        if not payload.item:
            raise HTTPException(400, "Escolha uma poção.")
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
    p.luta = L
    db.commit()
    return {"luta": luta_view(p), "resultado": None}


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
    if player.ryos < REST_COST:
        raise HTTPException(400, f"Ryos insuficientes: o descanso custa {REST_COST}.")
    player.ryos -= REST_COST
    player.vida_atual, player.chakra_atual = player.vida_max, player.chakra_max
    db.commit()
    return {"ok": True}


# ───────── Jornada: missões por tarefa (coleta, caçada, procurado), sem tempo ─────────
EXAMES = {6: ("Exame Genin", 5, "Genin"), 16: ("Exame Chunin", 12, "Chunin"),
          32: ("Exame Jonin", 20, "Jonin"), 54: ("Exame Anbu", 30, "Anbu")}
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
    objetivo = {"coleta": f"Colete {qtd} {v['recursos'][mid - 1]} em {mp['nome']}",
                "caca": f"Derrote {qtd} inimigos em {mp['nome']}",
                "busca": f"Capture {mp['chefe']}, procurado em {mp['nome']}"}[tipo]
    arco, sinopse = v["arcos"][((seq - 1) // 6) % len(v["arcos"])]
    if exame:
        titulo, historia = exame[0], f"O conselho de {v['nome']} avaliou seus feitos e abriu o {exame[0].lower()}."
    else:
        titulo, historia = f"{arco}: {TIPO_NOME[tipo]}", sinopse
    return {"seq": seq, "titulo": titulo, "historia": historia, "tipo": tipo, "mapa": mid, "objetivo": objetivo,
            "qtd": qtd, "req_nivel": req, "exame": exame[2] if exame else None, "rank": "DCBAS"[min(4, seq // 12)],
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
    sombra = payload.escolha == "sombra"
    ryos = int(m["ryos"] * (1.3 if sombra else 1))
    p.ryos += ryos
    p.fama += m["fama"]
    p.reputation = max(-100, min(100, p.reputation + (-8 if sombra else 5)))
    p.journey += 1
    p.journey_prog = 0
    promovido = None
    if m["exame"] and RANKS.index(p.rank) < RANKS.index(NinjaRank(m["exame"])):
        p.rank, promovido = NinjaRank(m["exame"]), m["exame"]
    levels = apply_exp(p, m["exp"])
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
                             .order_by(Player.level.desc()).limit(10)).all()
        minha = {"id": g.id, "nome": g.name, "nivel": g.level, "vitorias": g.vitorias_guerra,
                 "doado": g.recursos_doados, "proximo_nivel": 500 * g.level ** 2, "sou_lider": g.leader_id == p.id,
                 "total_membros": counts.get(g.id, 0),
                 "membros": [{"nome": m.ninja_name, "nivel": m.level, "rank": m.rank.value,
                              "lider": m.id == g.leader_id} for m in membros]}
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
    if db.get(Guild, payload.guild_id) is None:
        raise HTTPException(404, "Guilda não encontrada.")
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
        heir = db.scalar(select(Player).where(Player.guilda_id == g.id, Player.id != p.id)
                         .order_by(Player.level.desc()).limit(1))
        if heir:
            g.leader_id = heir.id
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
            leader.rank = NinjaRank.ANBU
        ch.rank = NinjaRank.KAGE
        v.leader_kage_id = ch.id
        v.challenger_id = v.challenger_since = None


def sannin_tick(db: Session) -> None:
    """Cadeiras de Sannin (3 no mundo). Vaga livre: o Anbu mais famoso, com 10.000+ de fama, assume na hora.
    Cadeiras cheias: o Anbu que superar a fama do Sannin mais fraco por 7 dias seguidos toma o lugar dele."""
    now = datetime.now(timezone.utc)
    sannins = list(db.scalars(select(Player).where(Player.rank == NinjaRank.SANNIN)))
    cands = list(db.scalars(select(Player).where(Player.rank == NinjaRank.ANBU, Player.fama >= SANNIN_MIN_FAMA)
                            .order_by(Player.fama.desc())))
    while cands and len(sannins) < SANNIN_VAGAS:
        c = cands.pop(0)
        c.rank, c.sannin_desde = NinjaRank.SANNIN, None
        sannins.append(c)
    fraco = min(sannins, key=lambda s: s.fama) if len(sannins) >= SANNIN_VAGAS else None
    for i, c in enumerate(cands):
        if i == 0 and fraco and c.fama > fraco.fama:
            if c.sannin_desde is None:
                c.sannin_desde = now
            elif now - as_utc(c.sannin_desde) >= timedelta(days=KAGE_DAYS):
                fraco.rank, c.rank, c.sannin_desde = NinjaRank.ANBU, NinjaRank.SANNIN, None
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
            "voce": {"rank": p.rank.value, "dias_restantes": dias}}


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
            "voce_e_kage": sou, "pode_desafiar": pode}


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
        raise HTTPException(403, "Exige ser Sannin, ter 20.000 de fama e mais fama que o Kage (e que o desafiante atual).")
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
        return [{"nome": p.ninja_name, "avatar": p.avatar, "vila": v, "rank": p.rank.value,
                 "nivel": p.level, "fama": p.fama} for p, v in rows]
    kages = []
    for v in db.scalars(select(Village).order_by(Village.id)):
        leader = db.get(Player, v.leader_kage_id) if v.leader_kage_id else None
        kages.append({"vila": v.name, "nome": leader.ninja_name if leader else v.npc_kage,
                      "fama": leader.fama if leader else v.npc_fama, "jogador": leader is not None})
    grads = []
    for rk in reversed(list(NinjaRank)):  # Kage -> Estudante
        rows = db.execute(select(Player, Village.name).join(Village, Village.id == Player.village_id)
                          .where(Player.rank == rk).order_by(Player.fama.desc(), Player.level.desc()).limit(5)).all()
        total = db.scalar(select(func.count()).select_from(Player).where(Player.rank == rk))
        grads.append({"rank": rk.value, "total": total, "top": [
            {"nome": p.ninja_name, "avatar": p.avatar, "vila": v, "rank": p.rank.value, "nivel": p.level, "fama": p.fama}
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
    elif attr == "chakra_attr":
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
          "energia": ("Pílula de Energia", "energia", 5, 300, 5)}
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
    return 12 * sum((item.status_bonus or {}).values())


def usar_pocao(p: Player, pid: str) -> str:
    if pid not in POCOES:
        raise HTTPException(404, "Poção desconhecida.")
    qtd = (p.pocoes or {}).get(pid, 0)
    if qtd <= 0:
        raise HTTPException(400, "Você não tem essa poção.")
    nome, ef, val, _, _ = POCOES[pid]
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
MAX_PETS = 20
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
    return {"pets": out, "max": MAX_PETS, "afinidade_max": AFINIDADE_MAX,
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
    if total >= MAX_PETS:
        raise HTTPException(409, f"Você já tem {MAX_PETS} pets. Solte algum antes de chocar.")
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
    """O que falta para a próxima patente: exame (nível e missão) ou fama, a partir de Anbu."""
    for seq, (nome, nivel, alvo) in sorted(EXAMES.items()):
        if RANKS.index(p.rank) < RANKS.index(NinjaRank(alvo)):
            return {"atual": p.rank.value, "proxima": alvo,
                    "texto": f"{nome}: chegue ao nível {nivel} (você: {p.level}) e à missão {seq} da Jornada (você: {p.journey + 1})."}
    if p.rank == NinjaRank.ANBU:
        return {"atual": p.rank.value, "proxima": "Sannin",
                "texto": f"Sannin: {_n(SANNIN_MIN_FAMA)} de fama e uma cadeira livre ou mais fama que o Sannin mais fraco por 7 dias (sua fama: {_n(p.fama)})."}
    if p.rank == NinjaRank.SANNIN:
        return {"atual": p.rank.value, "proxima": "Kage",
                "texto": f"Kage: {_n(KAGE_MIN_FAMA)} de fama e mais fama que o Kage da vila por 7 dias (sua fama: {_n(p.fama)})."}
    return {"atual": p.rank.value, "proxima": None, "texto": "Você alcançou a mais alta patente."}


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
    now = datetime.now(timezone.utc)
    info = hospital_info(p)
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
        quests.append({"nome": VILAS[vi]["recursos"][mis["mapa"] - 1], "qtd": p.journey_prog, "meta": mis["qtd"],
                       "mapa": mapa_vila(mis["mapa"], vi)["nome"]})
    return {"max": BOLSA_MAX, "slots": SLOTS,
            "itens": [{"id": i.id, "nome": i.item_name, "tipo": i.item_type.value, "bonus": i.status_bonus or {},
                       "equipado": i.is_equipped} for i in p.inventory],
            "consumiveis": [{"id": k, "nome": POCOES[k][0], "efeito": POCOES[k][1], "valor": POCOES[k][2], "qtd": q}
                            for k, q in (p.pocoes or {}).items() if q > 0 and k in POCOES],
            "materiais": [], "quests": quests}


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
