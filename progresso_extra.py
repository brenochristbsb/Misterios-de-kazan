"""
progresso_extra.py — Missões diárias e semanais, conquistas, login diário, eventos semanais e passe de temporada.

Registrado no final do api.py (app.include_router). Todo o estado fica em UMA coluna JSON do jogador (Player.progresso),
então a migração é automática (ensure_columns) e os dados antigos não são tocados.

Estrutura de Player.progresso:
    c      contadores acumulados de toda a vida (chefe, pvp, arena, dojo, forja...)
    d      {dia, p: contadores do dia, col: ids de missões já coletadas hoje}
    s      {sem, p: contadores da semana, col: ids coletados na semana}
    conq   ids de conquistas já coletadas ("metrica:indice")
    login  {ultimo, seq, melhor}
    passe  {temp, xp, col: níveis já coletados}
Dias e semanas viram à meia-noite UTC (a mesma régua da Arena).
"""
import copy
import random
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api import apply_exp, get_db, get_player
from game_extra import ARENA_INICIO, MAT_NOME, OVOS, POCOES, RestReq

router = APIRouter()

# ───────── Tempo ─────────
def _agora():
    return datetime.now(timezone.utc)


def _dia() -> str:
    return _agora().strftime("%Y-%m-%d")


def _semana() -> str:
    y, w, _ = _agora().isocalendar()
    return f"{y}-W{w:02d}"


def _dias_semana() -> int:
    return 7 - _agora().weekday()  # segunda a domingo; no domingo restam 1 dia (hoje)


PASSE_DIAS, PASSE_NIVEIS, PASSE_XP_NIVEL = 28, 30, 150


def _passe_temp() -> int:
    return max(0, (_agora().date() - ARENA_INICIO).days // PASSE_DIAS)


def _passe_dias() -> int:
    return (ARENA_INICIO + timedelta(days=(_passe_temp() + 1) * PASSE_DIAS) - _agora().date()).days


# ───────── Eventos semanais (um por semana, em rotação) ─────────
# meta: (contador, quantidade) com prêmio único por semana. efeito: bônus aplicado automaticamente.
EVENTOS = [
    {"id": "fortuna", "nome": "Festival da Fortuna", "desc": "+30% de ryos nas caçadas.", "ef": {"ryos": 30}, "meta": ("cacada", 40, "Vença 40 lutas")},
    {"id": "aprendiz", "nome": "Semana do Aprendiz", "desc": "+30% de XP nas caçadas.", "ef": {"exp": 30}, "meta": ("dojo", 6, "Faça 6 sessões no Dojo")},
    {"id": "chefes", "nome": "Caçada aos Chefes", "desc": "Chefes dão +60% de ryos e XP.", "ef": {"chefe": 60}, "meta": ("chefe", 6, "Derrote 6 chefes")},
    {"id": "arena", "nome": "Arena em Chamas", "desc": "+50% de moedas da Arena.", "ef": {"arena": 50}, "meta": ("arena_luta", 10, "Lute 10 vezes na Arena")},
    {"id": "passe", "nome": "Semana do Passe", "desc": "+50% de XP do passe.", "ef": {"passe": 50}, "meta": ("torre", 5, "Vença 5 andares da Torre")},
    {"id": "missoes", "nome": "Semana das Missões", "desc": "+50% de ryos e XP nas recompensas de missões.", "ef": {"missao": 50}, "meta": ("missao_ok", 6, "Complete 6 missões")},
]
EVENTO_PREMIO = {"ryos": 3000, "exp": 400, "arena": 50, "ovo": "ovo_incomum", "passe": 80}


def evento_atual() -> dict:
    dias = (_agora().date() - ARENA_INICIO).days
    return EVENTOS[(dias // 7) % len(EVENTOS)]


# ───────── Missões ─────────
# (id, contador, quantidade, texto, recompensa base)
DIARIAS = [
    ("d_cacada", "cacada", 6, "Vença 6 lutas nos mapas", {"ryos": 150, "exp": 60, "passe": 20}),
    ("d_chefe", "chefe", 1, "Derrote 1 chefe de mapa", {"ryos": 200, "exp": 90, "passe": 25}),
    ("d_arena", "arena_luta", 2, "Lute 2 vezes na Arena", {"ryos": 150, "exp": 60, "arena": 6, "passe": 20}),
    ("d_dojo", "dojo", 2, "Faça 2 sessões no Dojo", {"ryos": 120, "exp": 80, "passe": 20}),
    ("d_forja", "forja", 1, "Use a Forja 1 vez", {"ryos": 120, "exp": 60, "passe": 20}),
    ("d_torre", "torre", 2, "Vença 2 andares da Torre", {"ryos": 200, "exp": 90, "passe": 25}),
    ("d_doacao", "doacao", 1, "Doe ryos à sua guilda", {"ryos": 100, "exp": 50, "passe": 15}),
    ("d_gchefe", "gchefe", 1, "Lute contra o chefe da guilda", {"ryos": 180, "exp": 80, "passe": 25}),
    ("d_masm", "masmorra_andar", 2, "Vença 2 andares de masmorra", {"ryos": 200, "exp": 90, "passe": 25}),
    ("d_jornada", "jornada", 1, "Entregue 1 missão da Jornada", {"ryos": 200, "exp": 100, "passe": 25}),
]
SEMANAIS = [
    ("s_cacada", "cacada", 40, "Vença 40 lutas nos mapas", {"ryos": 900, "exp": 350, "arena": 25, "passe": 80}),
    ("s_chefe", "chefe", 6, "Derrote 6 chefes de mapa", {"ryos": 1000, "exp": 400, "arena": 30, "passe": 90}),
    ("s_arena", "arena_luta", 12, "Lute 12 vezes na Arena", {"ryos": 900, "exp": 350, "arena": 40, "passe": 80}),
    ("s_torre", "torre", 8, "Vença 8 andares da Torre", {"ryos": 1100, "exp": 450, "arena": 30, "passe": 90}),
    ("s_dojo", "dojo", 10, "Faça 10 sessões no Dojo", {"ryos": 800, "exp": 450, "arena": 20, "passe": 80}),
    ("s_forja", "forja", 6, "Use a Forja 6 vezes", {"ryos": 800, "exp": 350, "arena": 20, "passe": 80}),
    ("s_gchefe", "gchefe", 4, "Lute 4 vezes contra o chefe da guilda", {"ryos": 1000, "exp": 400, "arena": 30, "passe": 90}),
    ("s_masm", "masmorra_andar", 10, "Vença 10 andares de masmorra", {"ryos": 1100, "exp": 450, "arena": 30, "passe": 90}),
    ("s_pvp", "pvp", 5, "Vença 5 duelos contra jogadores", {"ryos": 900, "exp": 350, "arena": 25, "passe": 80}),
]
BONUS_DIA = {"ryos": 300, "exp": 150, "arena": 10, "pocao": ("vida_p", 2), "passe": 40}
BONUS_SEMANA = {"ryos": 2500, "exp": 800, "arena": 60, "ovo": "ovo_comum", "passe": 150}

# XP do passe ganho por ação (sem contar missões, conquistas e login, que têm o seu próprio)
PASSE_XP = {"cacada": 2, "chefe": 8, "pvp": 4, "arena_luta": 4, "torre": 6, "masmorra_andar": 5, "dojo": 3, "forja": 3,
            "doacao": 5, "gchefe": 8, "jornada": 12}

# ───────── Conquistas: (métrica, nome, textos, metas) — cada meta é um degrau com recompensa crescente ─────────
CONQ = [
    ("abates", "Caçador", "Derrotar {n} inimigos", [10, 50, 200, 1000, 5000]),
    ("chefe", "Matador de chefes", "Derrotar {n} chefes de mapa", [1, 10, 50, 200]),
    ("pvp", "Duelista", "Vencer {n} duelos contra jogadores", [1, 10, 50, 200]),
    ("arena", "Gladiador", "Vencer {n} lutas na Arena", [1, 10, 50, 200, 1000]),
    ("trofeus", "Campeão", "Chegar a {n} troféus", [100, 300, 700, 1200]),
    ("torre", "Escalador", "Chegar ao andar {n} da Torre", [5, 10, 25, 50, 100]),
    ("masmorra_andar", "Explorador", "Vencer {n} andares de masmorra", [5, 25, 100, 500]),
    ("dojo", "Discípulo", "Fazer {n} sessões no Dojo", [5, 25, 100, 500]),
    ("forja", "Ferreiro", "Usar a Forja {n} vezes", [5, 25, 100, 300]),
    ("doacao", "Benfeitor", "Doar à guilda {n} vezes", [1, 10, 50]),
    ("gchefe", "Guerreiro da guilda", "Lutar {n} vezes contra o chefe da guilda", [1, 10, 50]),
    ("jornada", "Viajante", "Entregar {n} missões da Jornada", [1, 5, 15, 30]),
    ("nivel", "Veterano", "Chegar ao nível {n}", [10, 25, 50, 75, 100]),
    ("fama", "Famoso", "Alcançar {n} de fama", [100, 1000, 10000, 50000]),
    ("login", "Fiel", "Entrar {n} dias seguidos", [3, 7, 14, 30]),
]
CONQ_RYOS = [300, 1000, 3000, 10000, 30000]
CONQ_PASSE = [20, 30, 40, 60, 80]
CONQ_ARENA = [0, 0, 20, 60, 150]

# ───────── Login diário: ciclo de 7 dias; faltar um dia reinicia a sequência ─────────
LOGIN = [
    {"ryos": 200},
    {"pocao": ("vida_p", 2)},
    {"ryos": 400, "mat": ("mat_1", 3)},
    {"arena": 10},
    {"ryos": 800, "pocao": ("vida_m", 1)},
    {"mat": ("mat_2", 3), "arena": 20},
    {"ryos": 1500, "ovo": "ovo_comum", "passe": 40},
]


def passe_recompensa(n: int) -> dict:
    rw = {"ryos": 150 * n}
    if n % 5 == 0:
        rw["arena"] = 30
    if n % 3 == 0:
        rw["pocao"] = ("vida_p", 2)
    if n == 10:
        rw["ovo"] = "ovo_comum"
    elif n == 20:
        rw["ovo"] = "ovo_incomum"
    elif n == PASSE_NIVEIS:
        rw["ovo"], rw["mat"] = "ovo_raro", ("mat_3", 5)
    return rw


# ───────── Estado ─────────
def _estado(p) -> dict:
    s = copy.deepcopy(p.progresso or {})
    s.setdefault("c", {})
    if (s.get("d") or {}).get("dia") != _dia():
        s["d"] = {"dia": _dia(), "p": {}, "col": []}
    if (s.get("s") or {}).get("sem") != _semana():
        s["s"] = {"sem": _semana(), "p": {}, "col": []}
    s.setdefault("conq", [])
    s.setdefault("login", {"ultimo": None, "seq": 0, "melhor": 0})
    if (s.get("passe") or {}).get("temp") != _passe_temp():
        s["passe"] = {"temp": _passe_temp(), "xp": 0, "col": []}
    return s


def _salvar(p, s: dict) -> None:
    p.progresso = s  # objeto novo: o SQLAlchemy não detecta mudança dentro de um JSON


def _passe_add(s: dict, xp: int) -> None:
    bonus = evento_atual()["ef"].get("passe", 0)
    s["passe"]["xp"] = min(PASSE_NIVEIS * PASSE_XP_NIVEL, s["passe"]["xp"] + int(xp * (1 + bonus / 100)))


def registrar(p, ev: str, n: int = 1) -> None:
    """Chamado pelo resto do jogo quando algo acontece (vitória, treino, doação...). Nunca deve derrubar a ação original."""
    try:
        s = _estado(p)
        for bloco in (s["c"], s["d"]["p"], s["s"]["p"]):
            bloco[ev] = bloco.get(ev, 0) + n
        if ev in PASSE_XP:
            _passe_add(s, PASSE_XP[ev] * n)
        _salvar(p, s)
    except Exception:
        pass


def registrar_luta(p, L: dict, res: dict, won: bool) -> None:
    """Gancho do fim_luta: contadores, bônus de evento e passe. Roda depois que as recompensas normais foram aplicadas."""
    t = L["tipo"]
    if t == "pvp":
        if won:
            registrar(p, "pvp")
    elif t == "arena":
        registrar(p, "arena_luta")
        if won:
            registrar(p, "arena")
        ar = res.get("arena")
        pct = evento_atual()["ef"].get("arena", 0)
        if ar and pct and ar["moedas"] > 0:
            extra = ar["moedas"] * pct // 100
            p.moedas_arena = (p.moedas_arena or 0) + extra
            ar["moedas"] += extra
            res.setdefault("extras", []).append(f"Evento: +{extra} moedas da Arena")
    elif t == "torre":
        if won and not (res.get("torre") or {}).get("falhou"):
            registrar(p, "torre")
    elif t == "masmorra":
        if won:
            registrar(p, "masmorra_andar")
    elif t == "gchefe":
        registrar(p, "gchefe")
    elif t not in ("clan",):  # caçada nos mapas
        if won:
            registrar(p, "cacada")
            if L.get("boss"):
                registrar(p, "chefe")
            ef = evento_atual()["ef"]
            pr, pe = ef.get("ryos", 0), ef.get("exp", 0)
            if L.get("boss") and ef.get("chefe"):
                pr, pe = pr + ef["chefe"], pe + ef["chefe"]
            if pr or pe:
                er, ee = (res.get("ryos") or 0) * pr // 100, (res.get("exp") or 0) * pe // 100
                if er:
                    p.ryos += er
                    res["ryos"] += er
                if ee:
                    res["niveis_ganhos"] = (res.get("niveis_ganhos") or 0) + apply_exp(p, ee)
                    res["exp"] += ee
                bonus = ", ".join(x for x in (f"+{er} ryos" if er else "", f"+{ee} XP" if ee else "") if x)
                if bonus:
                    res.setdefault("extras", []).append(f"Evento {evento_atual()['nome']}: {bonus}")


# ───────── Recompensas ─────────
def _mult(p) -> float:
    return 1 + p.level / 25


def _dar(p, s: dict, rw: dict, escala: bool = True, pct: int = 0) -> str:
    """Entrega uma recompensa e devolve o texto. `escala` ajusta ryos e XP ao nível; `pct` é o bônus de evento."""
    f = (_mult(p) if escala else 1) * (1 + pct / 100)
    partes = []
    if rw.get("ryos"):
        v = int(rw["ryos"] * f)
        p.ryos += v
        partes.append(f"{v:,} ryos".replace(",", "."))
    if rw.get("exp"):
        v = int(rw["exp"] * f)
        apply_exp(p, v)
        partes.append(f"{v} XP")
    if rw.get("arena"):
        p.moedas_arena = (p.moedas_arena or 0) + rw["arena"]
        partes.append(f"{rw['arena']} moedas da Arena")
    if rw.get("pocao"):
        pid, q = rw["pocao"]
        p.pocoes = {**(p.pocoes or {}), pid: (p.pocoes or {}).get(pid, 0) + q}
        partes.append(f"{q}x {POCOES[pid][0]}")
    if rw.get("mat"):
        mid, q = rw["mat"]
        p.materiais = {**(p.materiais or {}), mid: (p.materiais or {}).get(mid, 0) + q}
        partes.append(f"{q}x {MAT_NOME[mid]}")
    if rw.get("ovo"):
        p.ovos = {**(p.ovos or {}), rw["ovo"]: (p.ovos or {}).get(rw["ovo"], 0) + 1}
        partes.append(OVOS[rw["ovo"]][0])
    if rw.get("passe"):
        _passe_add(s, rw["passe"])
        partes.append(f"{rw['passe']} XP do passe")
    return ", ".join(partes)


def _txt_rw(p, rw: dict, escala: bool = True, pct: int = 0) -> dict:
    """Recompensa já escalada, para mostrar na tela (mesma conta do _dar)."""
    f = (_mult(p) if escala else 1) * (1 + pct / 100)
    out = dict(rw)
    for k in ("ryos", "exp"):
        if k in out:
            out[k] = int(out[k] * f)
    if "pocao" in out:
        out["pocao"] = [POCOES[out["pocao"][0]][0], out["pocao"][1]]
    if "mat" in out:
        out["mat"] = [MAT_NOME[out["mat"][0]], out["mat"][1]]
    if "ovo" in out:
        out["ovo"] = OVOS[out["ovo"]][0]
    return out


# ───────── Montagem das listas ─────────
def _sorteio(pool: list, chave: str, k: int) -> list:
    return random.Random(chave).sample(pool, k)


def _missoes(p, s: dict) -> dict:
    ev = evento_atual()
    pct = ev["ef"].get("missao", 0)

    def monta(lista, bloco, colet):
        itens = [{"id": i, "texto": t, "prog": min(q, bloco["p"].get(c, 0)), "qtd": q, "pronta": bloco["p"].get(c, 0) >= q,
                  "coletada": i in colet, "premio": _txt_rw(p, rw, True, pct)} for i, c, q, t, rw in lista]
        return itens

    dl = _sorteio(DIARIAS, f"{_dia()}-{p.id}", 3)
    sl = _sorteio(SEMANAIS, f"{_semana()}-{p.id}", 3)
    d, w = monta(dl, s["d"], s["d"]["col"]), monta(sl, s["s"], s["s"]["col"])
    return {"diarias": d, "semanais": w,
            "bonus_dia": {"pronto": all(x["coletada"] for x in d) and "d_tudo" not in s["d"]["col"], "coletado": "d_tudo" in s["d"]["col"],
                          "premio": _txt_rw(p, BONUS_DIA)},
            "bonus_semana": {"pronto": all(x["coletada"] for x in w) and "s_tudo" not in s["s"]["col"], "coletado": "s_tudo" in s["s"]["col"],
                             "premio": _txt_rw(p, BONUS_SEMANA)},
            "_sel": {x[0]: x for x in dl + sl}}


def _valor_conq(p, s: dict, m: str) -> int:
    if m == "abates":
        return sum((p.abates or {}).values())
    if m == "trofeus":
        return max(p.trofeus or 0, (p.arena or {}).get("melhor", 0))
    if m == "torre":
        return p.torre_andar or 0
    if m == "jornada":
        return p.journey
    if m == "nivel":
        return p.level
    if m == "fama":
        return p.fama
    if m == "login":
        return s["login"].get("melhor", 0)
    return s["c"].get(m, 0)


def _rw_conq(i: int, ultimo: bool) -> dict:
    rw = {"ryos": CONQ_RYOS[i], "passe": CONQ_PASSE[i]}
    if CONQ_ARENA[i]:
        rw["arena"] = CONQ_ARENA[i]
    if ultimo:
        rw["ovo"] = "ovo_incomum"
    return rw


def _conquistas(p, s: dict) -> list:
    out = []
    for m, nome, txt, metas in CONQ:
        v = _valor_conq(p, s, m)
        degraus = []
        for i, meta in enumerate(metas):
            cid = f"{m}:{i}"
            degraus.append({"id": cid, "texto": txt.format(n=f"{meta:,}".replace(",", ".")), "meta": meta, "ok": v >= meta,
                            "coletada": cid in s["conq"], "premio": _txt_rw(p, _rw_conq(i, i == len(metas) - 1), False)})
        out.append({"metrica": m, "nome": nome, "valor": v, "degraus": degraus})
    return out


def _login(s: dict) -> dict:
    lg = s["login"]
    hoje = _dia()
    ontem = (_agora() - timedelta(days=1)).strftime("%Y-%m-%d")
    pode = lg.get("ultimo") != hoje
    seq_ativa = lg.get("seq", 0) if lg.get("ultimo") in (hoje, ontem) else 0
    prox = seq_ativa + 1 if pode else seq_ativa
    return {"pode": pode, "seq": seq_ativa, "melhor": lg.get("melhor", 0), "dia_ciclo": (prox - 1) % 7 + 1 if prox else 1}


def _passe(p, s: dict) -> dict:
    xp = s["passe"]["xp"]
    nivel = min(PASSE_NIVEIS, xp // PASSE_XP_NIVEL)
    niveis = [{"n": n, "ok": n <= nivel, "coletado": n in s["passe"]["col"], "premio": _txt_rw(p, passe_recompensa(n), False)}
              for n in range(1, PASSE_NIVEIS + 1)]
    return {"temporada": s["passe"]["temp"] + 1, "dias": _passe_dias(), "xp": xp, "xp_nivel": PASSE_XP_NIVEL, "nivel": nivel,
            "max": PASSE_NIVEIS, "niveis": niveis}


def _pendentes(p, s: dict) -> int:
    m = _missoes(p, s)
    n = sum(1 for x in m["diarias"] + m["semanais"] if x["pronta"] and not x["coletada"])
    n += int(m["bonus_dia"]["pronto"]) + int(m["bonus_semana"]["pronto"]) + int(_login(s)["pode"])
    n += sum(1 for c in _conquistas(p, s) for d in c["degraus"] if d["ok"] and not d["coletada"])
    pa = _passe(p, s)
    n += sum(1 for x in pa["niveis"] if x["ok"] and not x["coletado"])
    return n


# ───────── Rotas ─────────
class MissaoReq(BaseModel):
    telegram_id: int
    id: str = Field(max_length=32)


class PasseReq(BaseModel):
    telegram_id: int
    nivel: int = Field(ge=1, le=PASSE_NIVEIS)


@router.get("/api/player/{telegram_id}/progresso")
def progresso_info(telegram_id: int, db: Session = Depends(get_db)):
    p = get_player(db, telegram_id)
    s = _estado(p)
    ev = evento_atual()
    meta_c, meta_q, meta_t = ev["meta"]
    m = _missoes(p, s)
    m.pop("_sel")
    atual = s["s"]["p"].get(meta_c, 0)
    return {
        "pendentes": _pendentes(p, s),
        "missoes": m, "dias_semana": _dias_semana(),
        "conquistas": _conquistas(p, s),
        "login": {**_login(s), "ciclo": [_txt_rw(p, r) for r in LOGIN]},
        "passe": _passe(p, s),
        "evento": {"id": ev["id"], "nome": ev["nome"], "desc": ev["desc"], "dias": _dias_semana(), "meta_texto": meta_t,
                   "prog": min(meta_q, atual), "qtd": meta_q, "pronta": atual >= meta_q, "coletada": "evento" in s["s"]["col"],
                   "premio": _txt_rw(p, EVENTO_PREMIO)},
    }


@router.post("/api/prog/missao/coletar")
def missao_coletar(payload: MissaoReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    s = _estado(p)
    pct = evento_atual()["ef"].get("missao", 0)
    m = _missoes(p, s)
    if payload.id in ("d_tudo", "s_tudo"):
        bloco, bonus, rw = (s["d"], m["bonus_dia"], BONUS_DIA) if payload.id == "d_tudo" else (s["s"], m["bonus_semana"], BONUS_SEMANA)
        if bonus["coletado"]:
            raise HTTPException(409, "Este bônus já foi coletado.")
        if not bonus["pronto"]:
            raise HTTPException(409, "Colete as 3 missões antes de pegar o bônus.")
    else:
        sel = m["_sel"].get(payload.id)
        if sel is None:
            raise HTTPException(404, "Missão não encontrada.")
        bloco = s["d"] if payload.id.startswith("d_") else s["s"]
        if payload.id in bloco["col"]:
            raise HTTPException(409, "Você já coletou esta missão.")
        if bloco["p"].get(sel[1], 0) < sel[2]:
            raise HTTPException(409, "A missão ainda não foi cumprida.")
        rw = sel[4]
    txt = _dar(p, s, rw, True, pct if not payload.id.endswith("_tudo") else 0)
    bloco["col"].append(payload.id)
    s["c"]["missao_ok"], s["s"]["p"]["missao_ok"] = s["c"].get("missao_ok", 0) + 1, s["s"]["p"].get("missao_ok", 0) + 1
    _salvar(p, s)
    db.commit()
    return {"mensagem": f"Recompensa: {txt}."}


@router.post("/api/prog/evento/coletar")
def evento_coletar(payload: RestReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    s = _estado(p)
    ev = evento_atual()
    if "evento" in s["s"]["col"]:
        raise HTTPException(409, "Você já coletou o prêmio deste evento.")
    if s["s"]["p"].get(ev["meta"][0], 0) < ev["meta"][1]:
        raise HTTPException(409, "A meta do evento ainda não foi cumprida.")
    txt = _dar(p, s, EVENTO_PREMIO)
    s["s"]["col"].append("evento")
    _salvar(p, s)
    db.commit()
    return {"mensagem": f"Prêmio do evento {ev['nome']}: {txt}."}


@router.post("/api/prog/conquista/coletar")
def conquista_coletar(payload: MissaoReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    s = _estado(p)
    for c in _conquistas(p, s):
        for i, d in enumerate(c["degraus"]):
            if d["id"] != payload.id:
                continue
            if d["coletada"]:
                raise HTTPException(409, "Você já coletou esta conquista.")
            if not d["ok"]:
                raise HTTPException(409, "A conquista ainda não foi cumprida.")
            txt = _dar(p, s, _rw_conq(i, i == len(c["degraus"]) - 1), False)
            s["conq"].append(payload.id)
            _salvar(p, s)
            db.commit()
            return {"mensagem": f"{c['nome']}: {txt}."}
    raise HTTPException(404, "Conquista não encontrada.")


@router.post("/api/prog/login/coletar")
def login_coletar(payload: RestReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    s = _estado(p)
    lg, hoje = s["login"], _dia()
    if lg.get("ultimo") == hoje:
        raise HTTPException(409, "Você já coletou a recompensa de hoje.")
    ontem = (_agora() - timedelta(days=1)).strftime("%Y-%m-%d")
    lg["seq"] = lg.get("seq", 0) + 1 if lg.get("ultimo") == ontem else 1
    lg["ultimo"], lg["melhor"] = hoje, max(lg.get("melhor", 0), lg["seq"])
    dia = (lg["seq"] - 1) % 7 + 1
    txt = _dar(p, s, {**LOGIN[dia - 1], "passe": LOGIN[dia - 1].get("passe", 0) + 10})
    _salvar(p, s)
    db.commit()
    return {"mensagem": f"Dia {dia} da sequência ({lg['seq']} seguidos): {txt}."}


@router.post("/api/prog/passe/coletar")
def passe_coletar(payload: PasseReq, db: Session = Depends(get_db)):
    p = get_player(db, payload.telegram_id, lock=True)
    s = _estado(p)
    if payload.nivel in s["passe"]["col"]:
        raise HTTPException(409, "Você já coletou este nível.")
    if s["passe"]["xp"] // PASSE_XP_NIVEL < payload.nivel:
        raise HTTPException(409, "Você ainda não chegou a este nível do passe.")
    rw = passe_recompensa(payload.nivel)
    txt = _dar(p, s, rw, False)
    s["passe"]["col"].append(payload.nivel)
    _salvar(p, s)
    db.commit()
    return {"mensagem": f"Nível {payload.nivel} do passe: {txt}."}
