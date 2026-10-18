"""
game_extra.py — Mapas com batalha PvE, drops, inventário, invocações, clãs e reputação.

Este módulo é registrado no final do api.py (app.include_router).
Mantenha api.py, models.py e game_extra.py na mesma pasta.
"""

import random

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from api import ATTRS, NO_CLAN_NAME, active_summon_link, apply_exp, effective_stats, get_db, get_player
from models import Clan, InventoryItem, ItemType, NinjaRank, PlayerSummon, Summon

router = APIRouter()

RANKS = list(NinjaRank)  # Estudante -> Kage
EXPLORE_CHAKRA = 15
CLAN_TEST_CHAKRA = 20
REST_COST = 50

# Mapas: (nome do drop, tipo, bônus, chance base). Chefes aparecem em 15% das explorações.
MAPS = [
    {"id": 1, "nome": "Floresta do Treino", "min_level": 1, "min_rank": "Estudante", "perigo": 1,
     "inimigo": "Lobo cinzento", "chefe": "Lobo Alfa",
     "drops": [("Kunai de Ferro", "Arma", {"taijutsu": 2}, 0.30),
               ("Pergaminho de Chakra", "Pergaminho", {"ninjutsu": 2}, 0.20),
               ("Traje de Treino", "Armadura", {"agilidade": 1}, 0.20)]},
    {"id": 2, "nome": "Vale da Névoa", "min_level": 5, "min_rank": "Genin", "perigo": 2,
     "inimigo": "Bandido mascarado", "chefe": "Líder dos Bandidos",
     "drops": [("Katana Sombria", "Arma", {"taijutsu": 4, "agilidade": 1}, 0.20),
               ("Traje Ninja", "Armadura", {"agilidade": 3}, 0.20),
               ("Pergaminho de Ilusão", "Pergaminho", {"genjutsu": 3}, 0.15)]},
    {"id": 3, "nome": "Vale da Morte", "min_level": 15, "min_rank": "Chunin", "perigo": 3,
     "inimigo": "Ninja renegado", "chefe": "Guardião Esquecido",
     "drops": [("Lâmina das Chamas", "Arma", {"ninjutsu": 6, "taijutsu": 2}, 0.15),
               ("Manto das Sombras", "Armadura", {"agilidade": 5, "genjutsu": 2}, 0.15)]},
]
MAPS_BY_ID = {m["id"]: m for m in MAPS}

# Requisitos de ingresso por clã (nível, rank). Clãs fora da lista usam o padrão.
CLAN_REQ = {"Clã do Olho Astral": (5, "Genin"), "Clã das Sombras Abissais": (10, "Genin")}
clan_req = lambda c: CLAN_REQ.get(c.name, (5, "Genin"))


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


def duel(me: dict, my_hp: int, foe: dict, foe_hp: int, max_rounds: int = 15):
    """Duelo por turnos (quem tem mais agilidade começa). Retorna (venceu, vida_restante, log)."""
    log = []
    my_first = me["agilidade"] >= foe["agilidade"]
    for rd in range(1, max_rounds + 1):
        for mine in ((True, False) if my_first else (False, True)):
            att, dfn = (me, foe) if mine else (foe, me)
            dmg = _hit(att, dfn)
            if mine:
                foe_hp -= dmg
            else:
                my_hp -= dmg
            who = "Você" if mine else "Inimigo"
            log.append(f"Rodada {rd}: {who} " + (f"causou {dmg} de dano" if dmg else "errou o golpe"))
            if foe_hp <= 0 or my_hp <= 0:
                break
        if foe_hp <= 0 or my_hp <= 0:
            break
    # Sem nocaute em 15 rodadas, vence quem tiver mais vida. Nunca deixa o jogador em 0.
    return my_hp > foe_hp, max(my_hp, 1), log


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
        "reputacao": p.reputation,
        "titulo": reputation_title(p.reputation),
        "inventario": [{"id": i.id, "nome": i.item_name, "tipo": i.item_type.value,
                        "bonus": i.status_bonus or {}, "equipado": i.is_equipped} for i in p.inventory],
        "invocacoes": [{"id": ps.summon_id, "nome": ps.summon.name, "nivel": ps.level, "ativo": ps.is_active,
                        "bonus_taijutsu": ps.summon.bonus_taijutsu, "bonus_ninjutsu": ps.summon.bonus_ninjutsu}
                       for ps in p.player_summons],
        "mapas": [{"id": m["id"], "nome": m["nome"], "min_level": m["min_level"], "min_rank": m["min_rank"],
                   "perigo": m["perigo"], "chefe": m["chefe"],
                   "drops": [{"nome": d[0], "tipo": d[1], "chance": d[3]} for d in m["drops"]]} for m in MAPS],
        "clas": [{"id": c.id, "nome": c.name, "passiva": c.passiva_atributo.value, "bonus": c.bonus_val,
                  "min_level": clan_req(c)[0], "min_rank": clan_req(c)[1]} for c in clans],
    }


@router.post("/api/maps/explore")
def explore(payload: ExploreReq, db: Session = Depends(get_db)):
    """Explora um mapa: batalha PvE contra um inimigo (ou chefe), com XP, ryos e drops."""
    player = get_player(db, payload.telegram_id, lock=True)
    mp = MAPS_BY_ID.get(payload.map_id)
    if mp is None:
        raise HTTPException(404, "Mapa não encontrado.")
    if player.level < mp["min_level"] or not rank_ok(player, mp["min_rank"]):
        raise HTTPException(403, f"Mapa bloqueado: exige nível {mp['min_level']} e rank {mp['min_rank']}.")
    if player.chakra_atual < EXPLORE_CHAKRA:
        raise HTTPException(400, f"Chakra insuficiente: precisa de {EXPLORE_CHAKRA}.")

    boss = random.random() < 0.15
    power = 3 * mp["perigo"] + 2
    foe = {a: int(power * (1.4 if boss else 1)) for a in ATTRS}
    foe_hp = int((60 + 30 * mp["perigo"]) * (1.6 if boss else 1))
    won, hp_left, log = duel(effective_stats(player), player.vida_atual, foe, foe_hp)

    player.chakra_atual -= EXPLORE_CHAKRA
    player.vida_atual = min(player.vida_atual, hp_left)
    mult = 2 if boss else 1
    exp, ryos, drops, novo_pet = 5, 0, [], None

    if won:
        exp, ryos = 25 * mp["perigo"] * mult, 40 * mp["perigo"] * mult
        player.ryos += ryos
        for nome, tipo, bonus, chance in mp["drops"]:
            if random.random() < chance * mult:
                db.add(InventoryItem(player_id=player.id, item_name=nome, item_type=ItemType(tipo), status_bonus=bonus))
                drops.append(nome)
        # A invocação ativa pode subir de nível; chefes podem soltar um contrato novo
        link = active_summon_link(player)
        if link and link.level < 10 and random.random() < 0.3:
            link.level += 1
        owned = {ps.summon_id for ps in player.player_summons}
        pool = db.scalars(select(Summon).where(Summon.id.not_in(owned))).all()
        if pool and random.random() < (0.15 if boss else 0.05):
            s = random.choice(pool)
            db.add(PlayerSummon(player_id=player.id, summon_id=s.id, level=1, is_active=not owned))
            novo_pet = s.name

    levels = apply_exp(player, exp)
    db.commit()
    return {"vitoria": won, "chefe": boss, "inimigo": mp["chefe"] if boss else mp["inimigo"], "log": log,
            "exp": exp, "ryos": ryos, "drops": drops, "novo_pet": novo_pet, "niveis_ganhos": levels}


@router.post("/api/inventory/equip")
def equip(payload: EquipReq, db: Session = Depends(get_db)):
    """Equipa/desequipa um item. Apenas um item equipado por tipo (arma, armadura, pergaminho)."""
    player = get_player(db, payload.telegram_id, lock=True)
    item = next((i for i in player.inventory if i.id == payload.item_id), None)
    if item is None:
        raise HTTPException(404, "Item não encontrado no seu inventário.")
    if payload.equipar:
        for other in player.inventory:
            if other.item_type == item.item_type:
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
    """Teste de admissão: duelo contra um examinador. Vitória = ingresso no clã."""
    player = get_player(db, payload.telegram_id, lock=True)
    clan = db.get(Clan, payload.clan_id)
    if clan is None or clan.name == NO_CLAN_NAME:
        raise HTTPException(404, "Clã não encontrado.")
    if player.clan.name != NO_CLAN_NAME:
        raise HTTPException(409, "Você já pertence a um clã.")
    min_level, min_rank = clan_req(clan)
    if player.level < min_level or not rank_ok(player, min_rank):
        raise HTTPException(403, f"Requisitos não atingidos: nível {min_level} e rank {min_rank}.")
    if player.chakra_atual < CLAN_TEST_CHAKRA:
        raise HTTPException(400, f"Chakra insuficiente: precisa de {CLAN_TEST_CHAKRA}.")

    power = int(3 + min_level * 1.5)  # o examinador acompanha a exigência do clã
    won, hp_left, log = duel(effective_stats(player), player.vida_atual,
                             {a: power for a in ATTRS}, 80 + 10 * min_level)
    player.chakra_atual -= CLAN_TEST_CHAKRA
    player.vida_atual = min(player.vida_atual, hp_left)
    if won:
        player.clan_id = clan.id
        apply_exp(player, 100)
    db.commit()
    msg = f"Você foi aceito no {clan.name}!" if won else "O examinador venceu. Treine e tente de novo."
    return {"entrou": won, "mensagem": msg, "log": log}


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
