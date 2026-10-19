"""
game_extra.py — Mapas com batalha PvE, drops, inventário, invocações, clãs e reputação.

Este módulo é registrado no final do api.py (app.include_router).
Mantenha api.py, models.py e game_extra.py na mesma pasta.
"""

import copy
import random
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api import ATTRS, NO_CLAN_NAME, active_summon_link, apply_exp, as_utc, effective_stats, get_db, get_player
from models import Clan, Guild, InventoryItem, ItemType, NinjaRank, Player, PlayerSummon, Summon, Village

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


# ───────── Combate por turnos ─────────
# Uma só fórmula para jogador, monstro e PvP: quem tem os melhores atributos leva vantagem, sem bônus escondidos.
NOME_ACAO = {"fisico": "um golpe físico", "ninjutsu": "um ninjutsu", "genjutsu": "um genjutsu"}


class AgirReq(BaseModel):
    telegram_id: int = Field(gt=0)
    acao: Literal["fisico", "ninjutsu", "genjutsu", "defender", "fugir"]


class PvpReq(BaseModel):
    telegram_id: int = Field(gt=0)
    alvo: str = Field(min_length=3, max_length=32)


def custo_tecnica(s: dict) -> int:
    """Chakra por técnica: a inteligência melhora o controle e barateia o custo."""
    return max(4, 12 - s["inteligencia"] // 3)


def golpe(att: dict, dfn: dict, acao: str):
    """Retorna (dano, crítico, atordoa). Dano 0 = esquivou."""
    if random.random() < 0.5 * dfn["agilidade"] / (dfn["agilidade"] + att["agilidade"] + 10):
        return 0, False, False
    if acao == "fisico":
        raw, red = att["taijutsu"] + att["forca"] * 0.8 + att["destreza"] * 0.4, dfn["defesa"] * 0.6
    elif acao == "ninjutsu":
        raw, red = att["ninjutsu"] * 1.3 + att["inteligencia"] * 0.5, dfn["defesa"] * 0.3
    else:  # genjutsu: a defesa mental é a inteligência
        raw, red = att["genjutsu"] + att["inteligencia"] * 0.8, dfn["inteligencia"] * 0.5
    dmg = (raw - red) * 1.3 * random.uniform(0.85, 1.15)
    crit = random.random() < att["destreza"] / (att["destreza"] + 60)
    if crit:
        dmg *= 1.5
    return max(1, int(dmg)), crit, acao == "genjutsu" and random.random() < 0.3


def _linha(quem: str, acao: str, dmg: int, crit: bool) -> str:
    if not dmg:
        return f"{quem} tentou {NOME_ACAO[acao]}, mas errou."
    return f"{quem} usou {NOME_ACAO[acao]}: {dmg} de dano" + (" (crítico!)" if crit else "") + "."


def turno_inimigo(L: dict, me: dict, p: Player) -> None:
    if L["atordoado"]:
        L["atordoado"] = False
        L["log"].append(f"{L['nome']} está atordoado e perde o turno.")
        return
    f, acao = L["stats"], "fisico"
    if L["chakra"] >= custo_tecnica(f) and random.random() < 0.4:
        acao = "ninjutsu" if f["ninjutsu"] >= f["genjutsu"] else "genjutsu"
        L["chakra"] -= custo_tecnica(f)
    dmg, crit, stun = golpe(f, me, acao)
    if dmg and L["defendendo"]:
        dmg = max(1, dmg // 2)
    L["defendendo"] = False
    p.vida_atual = max(0, p.vida_atual - dmg)
    L["log"].append(_linha(L["nome"], acao, dmg, crit) + (" Você ficou atordoado!" if stun and dmg else ""))
    if stun and dmg:
        L["me_atordoado"] = True


def iniciar_luta(p: Player, tipo: str, nome: str, stats: dict, hp: int, chakra: int, boss: bool = False, **ctx) -> None:
    me = effective_stats(p)
    L = {"tipo": tipo, "nome": nome, "boss": boss, "stats": stats, "hp": hp, "hp_max": hp, "chakra": chakra,
         "turno": 0, "log": [f"{nome} aparece diante de você."], "defendendo": False, "atordoado": False,
         "me_atordoado": False, **ctx}
    if stats["agilidade"] > me["agilidade"] or (stats["agilidade"] == me["agilidade"] and random.random() < 0.5):  # iniciativa: o mais ágil começa; empate no sorteio
        L["log"].append(f"{nome} é mais rápido e ataca primeiro.")
        turno_inimigo(L, me, p)
        p.vida_atual = max(1, p.vida_atual)
    p.luta = L


def luta_view(p: Player):
    L = p.luta
    if not L:
        return None
    return {"inimigo": L["nome"], "chefe": L["boss"], "tipo": L["tipo"], "hp": max(0, L["hp"]), "hp_max": L["hp_max"],
            "turno": L["turno"], "log": L["log"][-8:], "custo": custo_tecnica(effective_stats(p))}


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
            for nome, attr in mp["itens"]:  # drops temáticos: cada mapa solta só o tipo dele
                if random.random() < 0.25 * mult:
                    db.add(InventoryItem(player_id=p.id, item_name=nome, item_type=ItemType(mp["tipo"]),
                                         status_bonus={attr: 2 * mp["perigo"]}))
                    res["drops"].append(nome)
            if L["on_mission"]:  # coleta: 70% por vitória; caçada: 1 por vitória; procurado: só ao vencer o chefe
                mis = mission_for(p.journey + 1)
                gain = {"coleta": int(random.random() < 0.7), "caca": 1, "busca": int(L["boss"])}[mis["tipo"]]
                p.journey_prog = min(mis["qtd"], p.journey_prog + gain)
                res["missao"] = {"progresso": p.journey_prog, "qtd": mis["qtd"]}
            link = active_summon_link(p)
            if link and link.level < 10 and random.random() < 0.3:
                link.level += 1
            owned = {ps.summon_id for ps in p.player_summons}
            pool = db.scalars(select(Summon).where(Summon.id.not_in(owned))).all()
            if pool and random.random() < (0.15 if L["boss"] else 0.05):
                s = random.choice(pool)
                db.add(PlayerSummon(player_id=p.id, summon_id=s.id, level=1, is_active=not owned))
                res["novo_pet"] = s.name
    res["niveis_ganhos"] = apply_exp(p, res["exp"])
    return res


@router.get("/api/player/{telegram_id}/luta")
def luta_atual(telegram_id: int, db: Session = Depends(get_db)):
    return {"luta": luta_view(get_player(db, telegram_id))}


@router.post("/api/maps/explore")
def explore(payload: ExploreReq, db: Session = Depends(get_db)):
    """Explorar gasta 1 de energia e abre uma luta por turnos contra um inimigo do mapa."""
    player = get_player(db, payload.telegram_id, lock=True)
    mp = MAPS_BY_ID.get(payload.map_id)
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

    mis = mission_for(player.journey + 1)
    on_mission = mis["mapa"] == mp["id"] and player.level >= mis["req_nivel"] and player.journey_prog < mis["qtd"]
    boss = random.random() < (0.5 if on_mission and mis["tipo"] == "busca" else 0.15)
    power = int((2 * mp["perigo"] + 3) * (1.25 if boss else 1))
    player.energia -= 1
    iniciar_luta(player, "pve", mp["chefe"] if boss else mp["inimigo"], {a: power for a in ATTRS},
                 int((60 + 30 * mp["perigo"]) * (1.4 if boss else 1)), 60 + 20 * mp["perigo"], boss,
                 mapa=mp["id"], on_mission=on_mission)
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
    iniciar_luta(p, "pvp", t.ninja_name, {k: int(v) for k, v in effective_stats(t).items()}, t.vida_max, t.chakra_max)
    db.commit()
    return {"luta": luta_view(p)}


@router.post("/api/luta/agir")
def luta_agir(payload: AgirReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    if not p.luta:
        raise HTTPException(409, "Você não está em luta.")
    L, me, acao = copy.deepcopy(p.luta), effective_stats(p), payload.acao
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
    elif acao == "defender":
        L["defendendo"] = True
        p.chakra_atual = min(p.chakra_max, p.chakra_atual + 2)
        L["log"].append("Você se defende e recupera o fôlego.")
    else:
        if acao != "fisico":
            custo = custo_tecnica(me)
            if p.chakra_atual < custo:
                raise HTTPException(400, f"Chakra insuficiente: a técnica custa {custo}.")
            p.chakra_atual -= custo
        dmg, crit, stun = golpe(me, L["stats"], acao)
        L["hp"] -= dmg
        L["log"].append(_linha("Você", acao, dmg, crit) + (" O inimigo ficou atordoado!" if stun and dmg else ""))
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
    won, hp_left, log, ck = duel(effective_stats(player), player.vida_atual,
                                 {a: power for a in ATTRS}, 80 + 10 * min_level, chakra=player.chakra_atual)
    player.chakra_atual = ck
    player.vida_atual = min(player.vida_atual, hp_left)
    if won:
        player.clan_id = clan.id
        apply_exp(player, 25)
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


# ───────── Jornada: missões por tarefa (coleta, caçada, procurado), sem tempo ─────────
EXAMES = {6: ("Exame Genin", 5, "Genin"), 16: ("Exame Chunin", 12, "Chunin"),
          32: ("Exame Jonin", 20, "Jonin"), 54: ("Exame Anbu", 30, "Anbu")}
RECURSOS = {1: "Ervas de Treino", 2: "Brotos de Bambu Lunar", 3: "Cristais de Eco", 4: "Lodo Negro", 5: "Cacos de Geada"}
# História do primeiro arco: o Corvo Cinzento e o pergaminho partido
STORY = {
    1: ("O Pergaminho Partido", "Na biblioteca da academia, o selo de um pergaminho da vila foi encontrado partido. O mestre pede ervas para restaurá-lo."),
    2: ("Rastros no Bosque", "Lobos famintos rondam o bosque ao lado da academia. Alguém os está atraindo para longe da floresta."),
    3: ("O Procurado Cinzento", "O Livro Bingo da vila registra um nome novo: um chefe que serve ao misterioso Corvo Cinzento."),
    4: ("Penas Negras", "Penas negras surgem nos dormitórios. Reúna amostras para o mestre rastrear o mensageiro."),
    5: ("Voz na Neblina", "O Corvo mandou recrutas testar as defesas da academia. Detenha-os antes que cheguem lá."),
    6: ("Exame Genin", "O diretor reúne os melhores alunos. Provem que merecem o protetor de testa de Genin."),
    7: ("Além dos Muros", "Como Genin, sua primeira ordem é seguir a trilha do Corvo até os bambuzais."),
    8: ("Ecos do Bambuzal", "Os bambus guardam fragmentos de um mapa. Colete o que puder antes que o Corvo o faça."),
}
TIPO_NOME = {"coleta": "Coleta", "caca": "Caçada", "busca": "Procurado"}


def mission_for(seq: int) -> dict:
    """Missão nº `seq` (infinita). Tipos em rodízio: coleta, caçada e procurado do Livro Bingo. Exames promovem o rank."""
    exame = EXAMES.get(seq)
    req = exame[1] if exame else max(1, int(seq * 0.55))
    tipo = "caca" if exame else ("busca", "coleta", "caca")[seq % 3]
    mp = next((m for m in reversed(MAPS) if m["min_level"] <= req), MAPS[0])
    qtd = {"coleta": 4 + seq // 4, "caca": 5 + seq // 3, "busca": 1}[tipo]
    objetivo = {"coleta": f"Colete {qtd} {RECURSOS[mp['id']]} em {mp['nome']}",
                "caca": f"Derrote {qtd} inimigos em {mp['nome']}",
                "busca": f"Capture {mp['chefe']}, procurado em {mp['nome']}"}[tipo]
    titulo, historia = STORY.get(seq) or (
        (exame[0], f"O conselho avaliou seus feitos e abriu o {exame[0].lower()}.") if exame else
        (f"{TIPO_NOME[tipo]}: {mp['nome']}", "Novos rumores chegam à vila. Cumpra a tarefa e descubra o que o Corvo Cinzento planeja."))
    return {"seq": seq, "titulo": titulo, "historia": historia, "tipo": tipo, "mapa": mp["id"], "objetivo": objetivo,
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
    m = mission_for(p.journey + 1)
    return {**m, "progresso": p.journey_prog, "bloqueada": p.level < m["req_nivel"], "pronta": p.journey_prog >= m["qtd"]}


@router.post("/api/jornada/entregar")
def jornada_entregar(payload: JornadaDone, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    m = mission_for(p.journey + 1)
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
