"""
game_extra.py — Mapas com batalha PvE, drops, inventário, invocações, clãs e reputação.

Este módulo é registrado no final do api.py (app.include_router).
Mantenha api.py, models.py e game_extra.py na mesma pasta.
"""

import random
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api import ATTRS, NO_CLAN_NAME, active_summon_link, apply_exp, as_utc, effective_stats, get_db, get_player
from models import Clan, Guild, InventoryItem, ItemType, NinjaRank, Player, PlayerSummon, Summon

router = APIRouter()

RANKS = list(NinjaRank)  # Estudante -> Kage
EXPLORE_CHAKRA = 15
CLAN_TEST_CHAKRA = 20
REST_COST = 50

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
        "mapas": [{"id": m["id"], "nome": m["nome"], "min_level": m["min_level"], "max_level": m["max_level"],
                   "perigo": m["perigo"], "tipo": m["tipo"], "chefe": m["chefe"],
                   "itens": [i[0] for i in m["itens"]]} for m in MAPS],
        "clas": [{"id": c.id, "nome": c.name, "passiva": c.passiva_atributo.value, "bonus": c.bonus_val,
                  "min_level": clan_req(c)[0], "min_rank": clan_req(c)[1]} for c in clans],
    }


@router.post("/api/maps/explore")
def explore(payload: ExploreReq, db: Session = Depends(get_db)):
    """Explora um mapa: batalha PvE com XP, ryos e drops temáticos. Acima da faixa de nível, o XP cai."""
    player = get_player(db, payload.telegram_id, lock=True)
    mp = MAPS_BY_ID.get(payload.map_id)
    if mp is None:
        raise HTTPException(404, "Mapa não encontrado.")
    if player.level < mp["min_level"]:
        raise HTTPException(403, f"Mapa bloqueado: exige nível {mp['min_level']}.")
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
    over = player.level > mp["max_level"]
    exp, ryos, drops, novo_pet = 5, 0, [], None

    if won:
        exp = int(25 * mp["perigo"] * mult * (0.25 if over else 1))
        ryos = 40 * mp["perigo"] * mult
        player.ryos += ryos
        # Drops temáticos: cada mapa solta só o tipo de item dele
        for nome, attr in mp["itens"]:
            if random.random() < 0.25 * mult:
                db.add(InventoryItem(player_id=player.id, item_name=nome, item_type=ItemType(mp["tipo"]),
                                     status_bonus={attr: 2 * mp["perigo"]}))
                drops.append(nome)
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
            "exp": exp, "ryos": ryos, "drops": drops, "novo_pet": novo_pet, "niveis_ganhos": levels,
            "xp_reduzido": over}


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


# ───────── Jornada: missões infinitas com exames de promoção ─────────
EXAMES = {6: ("Exame Genin", 5, "Genin"), 16: ("Exame Chunin", 12, "Chunin"),
          32: ("Exame Jonin", 20, "Jonin"), 54: ("Exame Anbu", 30, "Anbu")}
ARCOS = ["Academia", "Fronteira", "Mercado das Sombras", "Rota dos Ventos", "Templo Esquecido", "Porto Nebuloso"]
FASES = ["Reconhecimento", "Escolta", "Resgate", "Emboscada", "Chefe do arco"]


def mission_for(seq: int) -> dict:
    """Missão nº `seq` da jornada (infinita). Concluir um exame promove o rank do jogador."""
    exame = EXAMES.get(seq)
    if exame:
        titulo, req, promove = exame
    else:
        titulo = f"{ARCOS[((seq - 1) // 5) % len(ARCOS)]}: {FASES[(seq - 1) % 5]}"
        req, promove = max(1, int(seq * 0.55)), None
    return {"seq": seq, "titulo": titulo, "rank": "DCBAS"[min(4, seq // 12)], "req_nivel": req, "exame": promove,
            "duracao": min(1800, 30 + 15 * seq) * (2 if exame else 1), "exp": 20 + 12 * seq, "ryos": 40 + 25 * seq}


class JornadaDone(BaseModel):
    telegram_id: int = Field(gt=0)
    # Honra sobe a reputação; sombra rende +30% de ryos, mas a derruba
    escolha: Literal["honra", "sombra"] = "honra"


@router.get("/api/player/{telegram_id}/jornada")
def jornada(telegram_id: int, db: Session = Depends(get_db)):
    """Missão atual da jornada. Missões já concluídas não são listadas."""
    p = get_player(db, telegram_id)
    m = mission_for(p.journey + 1)
    left = None
    if p.journey_ends_at:
        left = max(0, int((as_utc(p.journey_ends_at) - datetime.now(timezone.utc)).total_seconds()))
    return {**m, "bloqueada": p.level < m["req_nivel"], "em_andamento": p.journey_ends_at is not None, "restante": left}


@router.post("/api/jornada/iniciar")
def jornada_iniciar(payload: RestReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    m = mission_for(p.journey + 1)
    if p.journey_ends_at is not None:
        raise HTTPException(409, "Você já tem uma missão em andamento.")
    if p.level < m["req_nivel"]:
        raise HTTPException(403, f"Exige nível {m['req_nivel']}.")
    p.journey_ends_at = datetime.now(timezone.utc) + timedelta(seconds=m["duracao"])
    db.commit()
    return {"ok": True}


@router.post("/api/jornada/concluir")
def jornada_concluir(payload: JornadaDone, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    if p.journey_ends_at is None:
        raise HTTPException(409, "Nenhuma missão em andamento.")
    left = (as_utc(p.journey_ends_at) - datetime.now(timezone.utc)).total_seconds()
    if left > 0:
        raise HTTPException(409, f"A missão ainda não terminou: faltam {int(left) + 1}s.")
    m = mission_for(p.journey + 1)
    sombra = payload.escolha == "sombra"
    ryos = int(m["ryos"] * (1.3 if sombra else 1))
    p.ryos += ryos
    p.reputation = max(-100, min(100, p.reputation + (-8 if sombra else 5)))
    p.journey += 1
    p.journey_ends_at = None
    promovido = None
    if m["exame"] and RANKS.index(p.rank) < RANKS.index(NinjaRank(m["exame"])):
        p.rank, promovido = NinjaRank(m["exame"]), m["exame"]
    levels = apply_exp(p, m["exp"])
    db.commit()
    return {"exp": m["exp"], "ryos": ryos, "niveis_ganhos": levels, "promovido": promovido}


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
