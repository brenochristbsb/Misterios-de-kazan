"""
blocos_extra.py — Guilda completa, Nukenin e Livro Bingo, organizações criminosas, amigos, skins, sexo e pactos.

Registrado no final do api.py (app.include_router). Colunas novas no Player: blocos (JSON), nk_fama e org_id; tabela nova:
organizations. Tudo é criado/migrado sozinho (create_all e ensure_columns).

Regras de fama Nukenin (separada da fama de herói, 0 a 100.000):
    1.000  expulso da aldeia (entra no Livro Bingo, com recompensa pela cabeça; sem Hospital)
    2.000  pode fundar uma organização criminosa
    Derrotado em duelo por um caçador: perde fama, ryos e itens e volta à Academia. Se era da organização, só retorna a ela com
    1.500 + 1.000 por morte (2.500 na primeira). Se era o líder, o membro de maior fama Nukenin assume.
"""
import base64
import copy
import random
import re
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, object_session

from api import get_db, get_player
from game_extra import (BUFFS_GUILDA, MAT_NOME, RELIQUIAS, RestReq, _dados_set, _hoje, bolsa_livre, cargo_guilda, guild_limite, rank_ok)
from models import Guild, InventoryItem, ItemType, NinjaRank, Organizacao, Player, PlayerSummon, Summon

router = APIRouter()


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _b(p: Player) -> dict:
    return copy.deepcopy(p.blocos or {})


def _bs(p: Player, b: dict) -> None:
    p.blocos = b  # objeto novo: o SQLAlchemy não detecta mudança dentro de um JSON


def _por_nome(db: Session, nome: str) -> Player:
    t = db.scalar(select(Player).where(Player.ninja_name == nome))
    if t is None:
        raise HTTPException(404, "Jogador não encontrado.")
    return t


class Req(BaseModel):
    telegram_id: int = Field(gt=0)


class NomeReq(Req):
    nome: str = Field(min_length=3, max_length=32)


class MissaoG(Req):
    id: str = Field(max_length=32)


class BuffReq(Req):
    buff: str = Field(max_length=24)


class ConfigReq(Req):
    modo: Literal["aberta", "candidatura"]
    canal: str = Field(default="", max_length=80)


class EscudoReq(Req):
    imagem: str = Field(max_length=100_000)


class GidReq(Req):
    guild_id: int = Field(gt=0)


class CandReq(Req):
    pid: int = Field(gt=0)
    aceitar: bool


class ItemG(Req):
    item_id: int = Field(gt=0)


class IdxG(Req):
    idx: int = Field(ge=0)


# ═════════════════════════ GUILDA: missões, tesouraria, candidaturas, baú, escudo, config, ranking ═════════════════════════
GM_POOL = [("g_cacada", "cacada", 30, "Os membros vencem 30 lutas nos mapas"), ("g_chefe", "chefe", 3, "Os membros derrotam 3 chefes de mapa"),
           ("g_arena", "arena_luta", 6, "Os membros lutam 6 vezes na Arena"), ("g_dojo", "dojo", 8, "Os membros fazem 8 sessões no Dojo"),
           ("g_torre", "torre", 6, "Os membros vencem 6 andares da Torre"), ("g_forja", "forja", 5, "Os membros usam a Forja 5 vezes")]
GM_PREMIO = {"ryos": 250, "exp": 100, "arena": 6, "passe": 15}
GM_COFRE = 300  # cada coleta de missão também enche o cofre da guilda
BAU_MAX, BAU_RETIRADAS_DIA = 20, 1
ESCUDO_MAX_BYTES = 60_000


def _gm_sel(g: Guild) -> list:
    return random.Random(f"{_hoje()}-g{g.id}").sample(GM_POOL, 3)


def _gm(g: Guild) -> dict:
    d = copy.deepcopy((g.dados or {}).get("mdia") or {})
    return d if d.get("dia") == _hoje() else {"dia": _hoje(), "p": {}, "col": {}}


def _trava(db: Session, p: Player) -> Guild:
    """Guilda da pessoa, travada para escrita. O dict `dados` é regravado inteiro, então ninguém pode escrever ao mesmo tempo."""
    if not p.guilda_id:
        raise HTTPException(409, "Você não está em uma guilda.")
    db.flush()
    return db.scalar(select(Guild).where(Guild.id == p.guilda_id).with_for_update().execution_options(populate_existing=True))


def guilda_registrar(p: Player, ev: str, n: int = 1) -> None:
    """Gancho do progresso: soma o evento nas missões diárias da guilda. Nunca derruba a ação original."""
    try:
        if not p.guilda_id:
            return
        s = object_session(p)
        g = _trava(s, p)
        d = _gm(g)
        d["p"][ev] = d["p"].get(ev, 0) + n
        _dados_set(g, mdia=d)
    except Exception:
        pass


def _exige_vice(g: Guild, p: Player) -> None:
    if cargo_guilda(g, p.id) == "membro":
        raise HTTPException(403, "Só o líder e os vices podem fazer isso.")


def _guerra_ranking(db: Session) -> list:
    cont = dict(db.execute(select(Player.guilda_id, func.count()).where(Player.guilda_id.is_not(None)).group_by(Player.guilda_id)).all())
    gs = db.scalars(select(Guild).order_by(Guild.vitorias_guerra.desc(), Guild.level.desc(), Guild.recursos_doados.desc()).limit(10)).all()
    return [{"pos": i + 1, "id": g.id, "nome": g.name, "nivel": g.level, "vitorias": g.vitorias_guerra, "doado": int(g.recursos_doados),
             "membros": cont.get(g.id, 0), "escudo": (g.dados or {}).get("escudo")} for i, g in enumerate(gs)]


@router.get("/api/player/{telegram_id}/guilda2")
def guilda2_info(telegram_id: int, db: Session = Depends(get_db)):
    p = get_player(db, telegram_id)
    out = {"ranking": _guerra_ranking(db), "buffs_catalogo": {k: {"nome": v[0], "custo": v[1], "pct": v[2], "horas": v[3]} for k, v in BUFFS_GUILDA.items()}}
    g = p.guild if p.guilda_id else None
    if g is None:
        return {**out, "guilda": None}
    d, cargo, agora = g.dados or {}, cargo_guilda(g, p.id), _agora()
    m = _gm(g)
    mis = [{"id": i, "texto": t, "prog": min(q, m["p"].get(c, 0)), "qtd": q, "pronta": m["p"].get(c, 0) >= q, "coletada": i in m["col"].get(str(p.id), [])}
           for i, c, q, t in _gm_sel(g)]
    buffs = {k: v for k, v in (d.get("buffs") or {}).items() if datetime.fromisoformat(v) > agora}
    return {**out, "guilda": {
        "id": g.id, "nome": g.name, "cargo": cargo, "escudo": d.get("escudo"), "modo": d.get("modo", "aberta"), "canal": d.get("canal", ""),
        "cofre": d.get("cofre", 0), "buffs": [{"id": k, "nome": BUFFS_GUILDA[k][0], "ate": v} for k, v in buffs.items() if k in BUFFS_GUILDA],
        "missoes": mis, "premio": GM_PREMIO, "cofre_por_coleta": GM_COFRE,
        "candidaturas": d.get("cand", []) if cargo != "membro" else [],
        "bau": [{"i": i, "nome": it["n"], "bonus": {k: v for k, v in (it.get("b") or {}).items() if k[0] != "_"}} for i, it in enumerate(d.get("bau", []))],
        "bau_max": BAU_MAX, "retirou_hoje": (d.get("bau_ret") or {}).get(str(p.id)) == _hoje(),
        "membros": db.scalar(select(func.count()).select_from(Player).where(Player.guilda_id == g.id)), "limite": guild_limite(g)}}


@router.post("/api/gx/missao/coletar")
def gx_missao(payload: MissaoG, db: Session = Depends(get_db)):
    from progresso_extra import _dar, _estado, _salvar
    p = get_player(db, payload.telegram_id, lock=True)
    g = _trava(db, p)
    sel = {i: (c, q) for i, c, q, _ in _gm_sel(g)}
    if payload.id not in sel:
        raise HTTPException(404, "Missão não encontrada.")
    m = _gm(g)
    if m["p"].get(sel[payload.id][0], 0) < sel[payload.id][1]:
        raise HTTPException(409, "A missão da guilda ainda não foi cumprida.")
    col = m["col"].get(str(p.id), [])
    if payload.id in col:
        raise HTTPException(409, "Você já coletou esta missão.")
    m["col"][str(p.id)] = col + [payload.id]
    s = _estado(p)
    txt = _dar(p, s, GM_PREMIO)
    _salvar(p, s)
    _dados_set(g, mdia=m, cofre=(g.dados or {}).get("cofre", 0) + GM_COFRE)
    db.commit()
    return {"mensagem": f"Recompensa: {txt}. O cofre da guilda recebeu {GM_COFRE} ryos."}



@router.post("/api/gx/buff/comprar")
def gx_buff(payload: BuffReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    g = _trava(db, p)
    _exige_vice(g, p)
    b = BUFFS_GUILDA.get(payload.buff)
    if b is None:
        raise HTTPException(404, "Buff não encontrado.")
    nome, custo, pct, horas = b
    d = g.dados or {}
    if d.get("cofre", 0) < custo:
        raise HTTPException(400, f"O cofre da guilda não tem ryos suficientes: {nome} custa {custo}.")
    buffs = dict(d.get("buffs") or {})
    atual = datetime.fromisoformat(buffs[payload.buff]) if payload.buff in buffs else _agora()
    buffs[payload.buff] = (max(atual, _agora()) + timedelta(hours=horas)).isoformat()
    _dados_set(g, buffs=buffs, cofre=d.get("cofre", 0) - custo)
    db.commit()
    return {"mensagem": f"{nome} ativa: +{pct}% de XP e ryos nas caçadas de todos os membros por {horas}h."}



@router.post("/api/gx/config")
def gx_config(payload: ConfigReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    g = _trava(db, p)
    if g.leader_id != p.id:
        raise HTTPException(403, "Só o líder altera as configurações.")
    canal = payload.canal.strip()
    if canal and not re.fullmatch(r"https://t\.me/[A-Za-z0-9_+/\-]{3,64}", canal):
        raise HTTPException(400, "Use o link do canal ou grupo do Telegram, no formato https://t.me/...")
    _dados_set(g, modo=payload.modo, canal=canal)
    db.commit()
    return {"mensagem": "Configurações salvas."}



@router.post("/api/gx/escudo")
def gx_escudo(payload: EscudoReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    g = _trava(db, p)
    if g.leader_id != p.id:
        raise HTTPException(403, "Só o líder define o escudo.")
    m = re.fullmatch(r"data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)", payload.imagem)
    if not m:
        raise HTTPException(400, "Envie uma imagem PNG, JPEG ou WebP.")
    try:
        bruto = base64.b64decode(m.group(2), validate=True)
    except Exception:
        raise HTTPException(400, "Imagem inválida.")
    magia = {"png": bruto[:8] == b"\x89PNG\r\n\x1a\n", "jpeg": bruto[:3] == b"\xff\xd8\xff", "webp": bruto[:4] == b"RIFF" and bruto[8:12] == b"WEBP"}
    if len(bruto) > ESCUDO_MAX_BYTES or not magia[m.group(1)]:
        raise HTTPException(400, f"Imagem inválida ou maior que {ESCUDO_MAX_BYTES // 1000} KB.")
    _dados_set(g, escudo=payload.imagem)
    db.commit()
    return {"mensagem": "Escudo atualizado."}



@router.post("/api/gx/candidatar")
def gx_candidatar(payload: GidReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    if p.guilda_id:
        raise HTTPException(409, "Você já está em uma guilda.")
    g = db.scalar(select(Guild).where(Guild.id == payload.guild_id).with_for_update())
    if g is None:
        raise HTTPException(404, "Guilda não encontrada.")
    cand = list((g.dados or {}).get("cand", []))
    if any(c["pid"] == p.id for c in cand):
        raise HTTPException(409, "Você já se candidatou a esta guilda.")
    if len(cand) >= 20:
        raise HTTPException(409, "A fila de candidaturas desta guilda está cheia.")
    _dados_set(g, cand=cand + [{"pid": p.id, "nome": p.ninja_name, "nivel": p.level}])
    db.commit()
    return {"mensagem": f"Candidatura enviada à guilda {g.name}."}



@router.post("/api/gx/candidatura")
def gx_candidatura(payload: CandReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    g = _trava(db, p)
    _exige_vice(g, p)
    cand = list((g.dados or {}).get("cand", []))
    c = next((x for x in cand if x["pid"] == payload.pid), None)
    if c is None:
        raise HTTPException(404, "Candidatura não encontrada.")
    novo = [x for x in cand if x["pid"] != payload.pid]
    msg = "Candidatura recusada."
    if payload.aceitar:
        t = db.scalar(select(Player).where(Player.id == payload.pid).with_for_update())
        if t is None or t.guilda_id:
            msg = "Essa pessoa já entrou em outra guilda."
        elif db.scalar(select(func.count()).select_from(Player).where(Player.guilda_id == g.id)) >= guild_limite(g):
            raise HTTPException(409, f"A guilda está cheia (limite de {guild_limite(g)} membros).")
        else:
            t.guilda_id = g.id
            msg = f"{t.ninja_name} agora faz parte da guilda."
    _dados_set(g, cand=novo)
    db.commit()
    return {"mensagem": msg}



@router.post("/api/gx/bau/doar")
def gx_bau_doar(payload: ItemG, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    g = _trava(db, p)
    it = db.get(InventoryItem, payload.item_id)
    if it is None or it.player_id != p.id:
        raise HTTPException(404, "Item não encontrado.")
    if it.is_equipped:
        raise HTTPException(409, "Desequipe o item antes de doá-lo.")
    if it.item_name in RELIQUIAS:
        raise HTTPException(403, "Relíquias de clã são pessoais e não podem ser doadas.")
    bau = list((g.dados or {}).get("bau", []))
    if len(bau) >= BAU_MAX:
        raise HTTPException(409, f"O baú está cheio ({BAU_MAX} itens).")
    bau.append({"n": it.item_name, "t": it.item_type.value, "b": it.status_bonus or {}})
    db.delete(it)
    _dados_set(g, bau=bau)
    db.commit()
    return {"mensagem": f"{it.item_name} foi para o baú da guilda."}



@router.post("/api/gx/bau/pegar")
def gx_bau_pegar(payload: IdxG, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    g = _trava(db, p)
    d = g.dados or {}
    ret = dict(d.get("bau_ret") or {})
    if ret.get(str(p.id)) == _hoje():
        raise HTTPException(400, f"Cada membro retira {BAU_RETIRADAS_DIA} item por dia do baú.")
    bau = list(d.get("bau", []))
    if not 0 <= payload.idx < len(bau):
        raise HTTPException(404, "Esse item não está mais no baú.")
    if not bolsa_livre(p):
        raise HTTPException(409, "Mochila cheia. Libere espaço antes.")
    it = bau.pop(payload.idx)
    db.add(InventoryItem(player_id=p.id, item_name=it["n"], item_type=ItemType(it["t"]), status_bonus=it.get("b") or {}))
    ret[str(p.id)] = _hoje()
    _dados_set(g, bau=bau, bau_ret=ret)
    db.commit()
    return {"mensagem": f"Você pegou {it['n']} do baú."}



# ═════════════════════════ NUKENIN, LIVRO BINGO E ORGANIZAÇÕES ═════════════════════════
NK_EXPULSA, NK_ORG, NK_RETORNO, NK_MAX = 1000, 2000, 1500, 100_000
NK_RANKS = [(25000, "SSS"), (10000, "SS"), (5000, "S"), (3000, "A"), (2000, "B"), (1500, "C"), (1000, "D")]
ORG_FUNDAR_CUSTO, ORG_MAX = 5000, 10
# id: (nome, energia, ryos base, fama Nukenin, chance, material de drop)
ORG_MISSOES = {"contrabando": ("Contrabando de armas", 3, 120, 30, 0.80, "mat_2"),
               "roubo": ("Roubo de pergaminho proibido", 4, 220, 55, 0.70, "mat_3"),
               "assassinato": ("Assassinato por encomenda", 5, 380, 90, 0.60, "mat_4")}


def nk_rank(f: int) -> Optional[str]:
    return next((r for m, r in NK_RANKS if f >= m), None)


def nk_ganhar(p: Player, n: int) -> None:
    p.nk_fama = min(NK_MAX, (p.nk_fama or 0) + n)


def _prender(s: Session, t: Player) -> None:
    """Nukenin derrotado: perde fama, ryos e itens e volta à Academia. Se era de uma organização, só retorna com mais fama."""
    b = _b(t)
    b["nk_mortes"] = b.get("nk_mortes", 0) + 1
    org = t.org_id
    if org:
        b["retorno"] = {"org": org, "req": NK_RETORNO + 1000 * b["nk_mortes"]}
        t.org_id = None
        _org_sucessao(s, org, t.id)
    t.nk_fama, t.fama, t.ryos, t.journey, t.journey_prog = 0, 0, 0, 0, 0
    t.rank = NinjaRank.ESTUDANTE
    s.execute(delete(InventoryItem).where(InventoryItem.player_id == t.id))
    b["preso_em"] = _hoje()
    _bs(t, b)


def _org_sucessao(s: Session, org_id: int, saiu_id: int) -> None:
    o = s.get(Organizacao, org_id)
    if o is None or o.lider_id != saiu_id:
        return
    s.flush()
    prox = s.scalar(select(Player).where(Player.org_id == org_id, Player.id != saiu_id).order_by(Player.nk_fama.desc(), Player.id))
    if prox is None:
        s.delete(o)
    else:
        o.lider_id = prox.id


def nk_pos_pvp(p: Player, L: dict, res: dict, won: bool) -> None:
    """Gancho do fim da luta PvP: vencer um Nukenin do Livro Bingo paga a recompensa e prende o alvo."""
    if not won or not L.get("alvo_id"):
        return
    s = object_session(p)
    t = s.scalar(select(Player).where(Player.id == L["alvo_id"]).with_for_update())
    if t is None or (t.nk_fama or 0) < NK_EXPULSA:
        return
    premio = min(1_000_000, t.nk_fama * 10)
    p.ryos += premio
    p.fama += 20
    res.setdefault("extras", []).append(f"Livro Bingo: recompensa de {premio} ryos por prender {t.ninja_name}")
    _prender(s, t)


def _org_view(db: Session, o: Organizacao, p: Player) -> dict:
    ms = db.scalars(select(Player).where(Player.org_id == o.id).order_by(Player.nk_fama.desc())).all()
    return {"id": o.id, "nome": o.nome, "lider": next((m.ninja_name for m in ms if m.id == o.lider_id), "?"), "eu_lider": o.lider_id == p.id,
            "membros": [{"nome": m.ninja_name, "avatar": m.avatar, "nivel": m.level, "rank": nk_rank(m.nk_fama or 0) or "-", "lider": m.id == o.lider_id} for m in ms]}


@router.get("/api/player/{telegram_id}/nukenin")
def nukenin_info(telegram_id: int, db: Session = Depends(get_db)):
    p = get_player(db, telegram_id)
    b, f = p.blocos or {}, p.nk_fama or 0
    bingo = db.scalars(select(Player).where(Player.nk_fama >= NK_EXPULSA).order_by(Player.nk_fama.desc()).limit(30)).all()
    orgs = db.scalars(select(Organizacao).order_by(Organizacao.id)).all()
    cont = dict(db.execute(select(Player.org_id, func.count()).where(Player.org_id.is_not(None)).group_by(Player.org_id)).all())
    o = db.get(Organizacao, p.org_id) if p.org_id else None
    dia = b.get("org_dia") or {}
    feitas = dia.get("feitas", []) if dia.get("dia") == _hoje() else []
    return {"fama": f, "rank": nk_rank(f), "ativo": f >= NK_EXPULSA, "proximo": next(((m, r) for m, r in sorted(NK_RANKS) if f < m), None),
            "mortes": b.get("nk_mortes", 0), "pode_fundar": f >= NK_ORG and not p.org_id, "marcos": {"expulso": NK_EXPULSA, "org": NK_ORG, "retorno": NK_RETORNO},
            "retorno": b.get("retorno"), "ilegal": {"energia": 3, "nivel": 10},
            "bingo": [{"nome": x.ninja_name, "avatar": x.avatar, "nivel": x.level, "rank": nk_rank(x.nk_fama), "fama": x.nk_fama, "recompensa": min(1_000_000, x.nk_fama * 10),
                       "org": next((y.nome for y in orgs if y.id == x.org_id), None), "eu": x.id == p.id} for x in bingo],
            "org": _org_view(db, o, p) if o else None,
            "orgs": [{"id": y.id, "nome": y.nome, "membros": cont.get(y.id, 0), "max": ORG_MAX} for y in orgs],
            "missoes": [{"id": k, "nome": v[0], "energia": v[1], "fama": v[3], "chance": int(v[4] * 100), "feita": k in feitas} for k, v in ORG_MISSOES.items()],
            "fundar_custo": ORG_FUNDAR_CUSTO}


@router.post("/api/nk/ilegal")
def nk_ilegal(payload: Req, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    if p.luta or p.masmorra or p.torre_sessao:
        raise HTTPException(409, "Termine o que está fazendo antes.")
    if p.level < 10:
        raise HTTPException(403, "Missões ilegais exigem nível 10.")
    if p.energia < 3:
        raise HTTPException(400, "Energia insuficiente: custa 3.")
    if p.vida_atual < 0.3 * p.vida_max:
        raise HTTPException(400, "Vida baixa demais (mínimo 30%).")
    p.energia -= 3
    if random.random() < 0.7:
        ryos = 100 + 20 * p.level
        p.ryos += ryos
        nk_ganhar(p, 30)
        p.reputation = max(-100, p.reputation - 3)
        msg = f"Trabalho sujo concluído: {ryos} ryos e +30 de fama Nukenin."
    else:
        p.vida_atual = max(1, p.vida_atual - p.vida_max // 4)
        nk_ganhar(p, 10)
        msg = "A guarda quase te pegou: você perdeu 25% de vida e ganhou +10 de fama Nukenin."
    db.commit()
    return {"mensagem": msg}


class OrgNome(Req):
    nome: str = Field(min_length=3, max_length=32)


@router.post("/api/org/fundar")
def org_fundar(payload: OrgNome, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    if p.org_id:
        raise HTTPException(409, "Você já está em uma organização.")
    if (p.nk_fama or 0) < NK_ORG:
        raise HTTPException(403, f"Fundar uma organização exige {NK_ORG} de fama Nukenin.")
    if p.ryos < ORG_FUNDAR_CUSTO:
        raise HTTPException(400, f"Fundar custa {ORG_FUNDAR_CUSTO} ryos.")
    nome = payload.nome.strip()
    if not re.fullmatch(r"[A-Za-zÀ-ÿ0-9 ]{3,32}", nome):
        raise HTTPException(400, "Use letras, números e espaços (3 a 32 caracteres).")
    if db.scalar(select(Organizacao.id).where(Organizacao.nome == nome)):
        raise HTTPException(409, "Já existe uma organização com esse nome.")
    o = Organizacao(nome=nome, lider_id=p.id)
    db.add(o)
    db.flush()
    p.org_id = o.id
    p.ryos -= ORG_FUNDAR_CUSTO
    db.commit()
    return {"mensagem": f"Organização {nome} fundada."}


class OrgId(Req):
    org_id: int = Field(gt=0)


@router.post("/api/org/entrar")
def org_entrar(payload: OrgId, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    f = p.nk_fama or 0
    if p.org_id:
        raise HTTPException(409, "Você já está em uma organização.")
    if f < NK_EXPULSA:
        raise HTTPException(403, f"Só Nukenin entram em organizações ({NK_EXPULSA} de fama Nukenin).")
    o = db.scalar(select(Organizacao).where(Organizacao.id == payload.org_id).with_for_update())
    if o is None:
        raise HTTPException(404, "Organização não encontrada.")
    b = _b(p)
    ret = b.get("retorno")
    if ret and ret["org"] == o.id and f < ret["req"]:
        raise HTTPException(403, f"Para voltar a esta organização você precisa de {ret['req']} de fama Nukenin (você tem {f}).")
    if db.scalar(select(func.count()).select_from(Player).where(Player.org_id == o.id)) >= ORG_MAX:
        raise HTTPException(409, "A organização está cheia.")
    p.org_id = o.id
    b.pop("retorno", None) if ret and ret["org"] == o.id else None
    _bs(p, b)
    db.commit()
    return {"mensagem": f"Você entrou em {o.nome}."}


@router.post("/api/org/sair")
def org_sair(payload: Req, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    if not p.org_id:
        raise HTTPException(409, "Você não está em uma organização.")
    org = p.org_id
    p.org_id = None
    _org_sucessao(db, org, p.id)
    db.commit()
    return {"mensagem": "Você deixou a organização."}


class OrgMissao(Req):
    id: str = Field(max_length=16)


@router.post("/api/org/missao")
def org_missao(payload: OrgMissao, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    m = ORG_MISSOES.get(payload.id)
    if m is None:
        raise HTTPException(404, "Missão não encontrada.")
    if not p.org_id:
        raise HTTPException(403, "Só membros de uma organização fazem essas missões.")
    if p.luta or p.masmorra or p.torre_sessao:
        raise HTTPException(409, "Termine o que está fazendo antes.")
    nome, en, ryos, fama, chance, mat = m
    if p.energia < en:
        raise HTTPException(400, f"Energia insuficiente: custa {en}.")
    if p.vida_atual < 0.3 * p.vida_max:
        raise HTTPException(400, "Vida baixa demais (mínimo 30%).")
    b = _b(p)
    dia = b.get("org_dia") if (b.get("org_dia") or {}).get("dia") == _hoje() else {"dia": _hoje(), "feitas": []}
    if payload.id in dia["feitas"]:
        raise HTTPException(409, "Você já fez esta missão hoje.")
    dia["feitas"].append(payload.id)
    b["org_dia"] = dia
    _bs(p, b)
    p.energia -= en
    if random.random() < chance:
        r = int((ryos + 15 * p.level) * (1 + (p.nk_fama or 0) / 20000))
        p.ryos += r
        nk_ganhar(p, fama)
        q = random.randint(1, 3)
        p.materiais = {**(p.materiais or {}), mat: (p.materiais or {}).get(mat, 0) + q}
        msg = f"{nome}: {r} ryos, {q}x {MAT_NOME[mat]} e +{fama} de fama Nukenin."
    else:
        p.vida_atual = max(1, p.vida_atual - p.vida_max // 5)
        nk_ganhar(p, fama // 4)
        msg = f"{nome} deu errado: você perdeu 20% de vida."
    db.commit()
    return {"mensagem": msg}


# ═════════════════════════ PERFIL: amigos, skins e sexo ═════════════════════════
AMIGOS_MAX = 50
SKINS = {"padrao": ("Ninja comum", "🥷", 0, 0), "sombra": ("Sombra", "🌑", 3000, 0), "chama": ("Chama", "🔥", 6000, 0), "gelo": ("Geada", "❄️", 6000, 0),
         "trovao": ("Trovão", "⚡", 8000, 0), "sakura": ("Sakura", "🌸", 0, 60), "dragao": ("Dragão", "🐉", 0, 150), "lenda": ("Lenda", "👑", 0, 400)}
SEXO_CUSTO = 1000


def _amigos_ids(p: Player) -> list:
    return list((p.blocos or {}).get("amigos", []))


@router.get("/api/player/{telegram_id}/social2")
def social2_info(telegram_id: int, db: Session = Depends(get_db)):
    p = get_player(db, telegram_id)
    b = p.blocos or {}
    ids = b.get("amigos", []) + b.get("pedidos", [])
    por_id = {x.id: x for x in db.scalars(select(Player).where(Player.id.in_(ids))).all()} if ids else {}

    def v(i):
        x = por_id.get(i)
        return {"nome": x.ninja_name, "avatar": x.avatar, "nivel": x.level, "skin": SKINS.get((x.blocos or {}).get("skin", "padrao"), SKINS["padrao"])[1]} if x else None
    own = ["padrao"] + [s for s in b.get("skins", []) if s in SKINS]
    return {"amigos": [x for x in map(v, b.get("amigos", [])) if x], "pedidos": [x for x in map(v, b.get("pedidos", [])) if x],
            "skin": b.get("skin", "padrao"), "skins": [{"id": k, "nome": s[0], "emoji": s[1], "ryos": s[2], "arena": s[3], "tenho": k in own} for k, s in SKINS.items()],
            "sexo": b.get("sexo"), "sexo_custo": SEXO_CUSTO, "nk_fama": p.nk_fama or 0, "max": AMIGOS_MAX}


@router.post("/api/amigos/pedir")
def amigos_pedir(payload: NomeReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    t = _por_nome(db, payload.nome)
    if t.id == p.id:
        raise HTTPException(400, "Você não pode adicionar a si mesmo.")
    b, bt = _b(p), _b(t)
    if t.id in b.get("amigos", []):
        raise HTTPException(409, "Vocês já são amigos.")
    if len(b.get("amigos", [])) >= AMIGOS_MAX:
        raise HTTPException(409, f"Limite de {AMIGOS_MAX} amigos.")
    if t.id in b.get("pedidos", []):  # ele já tinha pedido: vira amizade na hora
        b["pedidos"].remove(t.id)
        b.setdefault("amigos", []).append(t.id)
        bt.setdefault("amigos", []).append(p.id)
        _bs(p, b), _bs(t, bt)
        db.commit()
        return {"mensagem": f"Agora você e {t.ninja_name} são amigos."}
    if p.id in bt.get("pedidos", []):
        raise HTTPException(409, "Você já enviou um pedido para essa pessoa.")
    if len(bt.get("pedidos", [])) >= 30:
        raise HTTPException(409, "Essa pessoa tem pedidos demais pendentes.")
    bt.setdefault("pedidos", []).append(p.id)
    _bs(t, bt)
    db.commit()
    return {"mensagem": f"Pedido enviado para {t.ninja_name}."}


class RespReq(NomeReq):
    aceitar: bool


@router.post("/api/amigos/responder")
def amigos_responder(payload: RespReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    t = _por_nome(db, payload.nome)
    b = _b(p)
    if t.id not in b.get("pedidos", []):
        raise HTTPException(404, "Pedido não encontrado.")
    b["pedidos"].remove(t.id)
    msg = "Pedido recusado."
    if payload.aceitar:
        if len(b.get("amigos", [])) >= AMIGOS_MAX:
            raise HTTPException(409, f"Limite de {AMIGOS_MAX} amigos.")
        bt = _b(t)
        b.setdefault("amigos", []).append(t.id)
        bt.setdefault("amigos", []).append(p.id)
        _bs(t, bt)
        msg = f"Agora você e {t.ninja_name} são amigos."
    _bs(p, b)
    db.commit()
    return {"mensagem": msg}


@router.post("/api/amigos/remover")
def amigos_remover(payload: NomeReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    t = _por_nome(db, payload.nome)
    b, bt = _b(p), _b(t)
    b["amigos"] = [i for i in b.get("amigos", []) if i != t.id]
    bt["amigos"] = [i for i in bt.get("amigos", []) if i != p.id]
    _bs(p, b), _bs(t, bt)
    db.commit()
    return {"mensagem": f"{t.ninja_name} foi removido dos amigos."}


class SkinReq(Req):
    skin: str = Field(max_length=16)
    acao: Literal["comprar", "equipar"]


@router.post("/api/skin")
def skin(payload: SkinReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    s = SKINS.get(payload.skin)
    if s is None:
        raise HTTPException(404, "Skin não encontrada.")
    b = _b(p)
    tenho = payload.skin == "padrao" or payload.skin in b.get("skins", [])
    if payload.acao == "comprar":
        if tenho:
            raise HTTPException(409, "Você já tem esta skin.")
        if p.ryos < s[2] or (p.moedas_arena or 0) < s[3]:
            raise HTTPException(400, "Recursos insuficientes para esta skin.")
        p.ryos -= s[2]
        p.moedas_arena = (p.moedas_arena or 0) - s[3]
        b["skins"] = b.get("skins", []) + [payload.skin]
        msg = f"Você comprou a skin {s[0]}."
    else:
        if not tenho:
            raise HTTPException(403, "Você ainda não tem esta skin.")
        b["skin"] = payload.skin
        msg = f"Skin {s[0]} equipada."
    _bs(p, b)
    db.commit()
    return {"mensagem": msg}


class SexoReq(Req):
    sexo: Literal["M", "F"]


@router.post("/api/perfil/sexo")
def trocar_sexo(payload: SexoReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    b = _b(p)
    if b.get("sexo") == payload.sexo:
        raise HTTPException(409, "Seu personagem já é assim.")
    custo = SEXO_CUSTO if b.get("sexo") else 0  # a primeira escolha é grátis
    if p.ryos < custo:
        raise HTTPException(400, f"Trocar custa {custo} ryos.")
    p.ryos -= custo
    b["sexo"] = payload.sexo
    _bs(p, b)
    db.commit()
    return {"mensagem": "Sexo do personagem atualizado."}


# ═════════════════════════ PACTOS com seres míticos (Jounin ou mais) ═════════════════════════
# Cada pacto é uma invocação (Summon) do jogo: o bônus entra nos atributos como qualquer invocação ativa e cresce 20% por nível.
PACTOS = {"kirin": ("Kirin dos Trovões", 6, 8, 20000), "tengu": ("Tengu do Vento", 9, 5, 20000), "byakko": ("Byakko, o Tigre Branco", 12, 3, 25000),
          "seiryu": ("Seiryu, o Dragão Azul", 4, 13, 25000), "genbu": ("Genbu, a Tartaruga Negra", 7, 7, 30000)}
PACTO_MAX_NIVEL, PACTO_MAX_QTD = 10, 3


def _summon_pacto(db: Session, pid: str) -> Summon:
    nome, tai, nin, _ = PACTOS[pid]
    s = db.scalar(select(Summon).where(Summon.name == nome))
    if s is None:
        s = Summon(name=nome, level=1, bonus_taijutsu=tai, bonus_ninjutsu=nin)
        db.add(s)
        db.flush()
    return s


@router.get("/api/player/{telegram_id}/pactos")
def pactos_info(telegram_id: int, db: Session = Depends(get_db)):
    p = get_player(db, telegram_id)
    meus = {ps.summon.name: ps for ps in p.player_summons}
    return {"liberado": rank_ok(p, "Jonin"), "max": PACTO_MAX_QTD, "max_nivel": PACTO_MAX_NIVEL, "pactos": [
        {"id": k, "nome": v[0], "taijutsu": v[1], "ninjutsu": v[2], "preco": v[3], "tenho": v[0] in meus, "nivel": meus[v[0]].level if v[0] in meus else 0,
         "ativo": bool(v[0] in meus and meus[v[0]].is_active), "summon_id": meus[v[0]].summon_id if v[0] in meus else None,
         "custo_up": {"ryos": 3000 * meus[v[0]].level, "mat": meus[v[0]].level} if v[0] in meus and meus[v[0]].level < PACTO_MAX_NIVEL else None}
        for k, v in PACTOS.items()], "material": MAT_NOME["mat_4"]}


class PactoReq(Req):
    pacto: str = Field(max_length=16)


def _pacto(payload: PactoReq):
    if payload.pacto not in PACTOS:
        raise HTTPException(404, "Pacto não encontrado.")
    return PACTOS[payload.pacto]


@router.post("/api/pacto/firmar")
def pacto_firmar(payload: PactoReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    nome, _, _, preco = _pacto(payload)
    if not rank_ok(p, "Jonin"):
        raise HTTPException(403, "Pactos com seres míticos exigem a patente Jonin.")
    if any(ps.summon.name == nome for ps in p.player_summons):
        raise HTTPException(409, "Você já firmou este pacto.")
    if sum(1 for ps in p.player_summons if ps.summon.name in {v[0] for v in PACTOS.values()}) >= PACTO_MAX_QTD:
        raise HTTPException(409, f"Você só pode ter {PACTO_MAX_QTD} pactos.")
    if p.ryos < preco:
        raise HTTPException(400, f"Ryos insuficientes: o pacto custa {preco}.")
    s = _summon_pacto(db, payload.pacto)
    p.ryos -= preco
    ativo = not any(ps.is_active for ps in p.player_summons)
    db.add(PlayerSummon(player_id=p.id, summon_id=s.id, level=1, is_active=ativo))
    db.commit()
    return {"mensagem": f"Pacto firmado com {nome}." + (" Ele já acompanha você." if ativo else " Ative-o quando quiser.")}


@router.post("/api/pacto/fortalecer")
def pacto_fortalecer(payload: PactoReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    nome = _pacto(payload)[0]
    ps = next((x for x in p.player_summons if x.summon.name == nome), None)
    if ps is None:
        raise HTTPException(404, "Você não firmou este pacto.")
    if ps.level >= PACTO_MAX_NIVEL:
        raise HTTPException(409, "Este pacto já está no nível máximo.")
    ryos, mat = 3000 * ps.level, ps.level
    if p.ryos < ryos:
        raise HTTPException(400, f"Ryos insuficientes: custa {ryos}.")
    if (p.materiais or {}).get("mat_4", 0) < mat:
        raise HTTPException(400, f"Faltam materiais: {mat}x {MAT_NOME['mat_4']}.")
    p.ryos -= ryos
    p.materiais = {**p.materiais, "mat_4": p.materiais["mat_4"] - mat}
    ps.level += 1
    db.commit()
    return {"mensagem": f"{nome} agora está no nível {ps.level}."}
