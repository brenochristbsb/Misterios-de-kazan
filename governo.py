"""governo.py - Patentes, Sannins, Trono do Kage e Guerra das Vilas (GDD 18, 25-29).

Tudo aqui e decidido pelo servidor. Funcoes recebem `db` (sqlite3) e devem ser chamadas
dentro de tx() quando escrevem. O relogio vem de agora(), que os testes podem trocar.
"""
from __future__ import annotations
import json
import random
from datetime import date, datetime, timedelta, timezone

import combate_reativo as C

FMT = "%Y-%m-%d %H:%M:%S"
PATENTES = [("Estudante", 0), ("Genin", 100), ("Chunin", 500), ("Jounin", 2000), ("Jounin Elite", 6000)]
FAMA_KAGE = 20000            # fama de carreira minima para ser Kage (GDD 27)
PCT_DESAFIO = 0.90           # o desafiante precisa de 90% da fama do Kage (aprovado)
ESCUDO_H = 48                # Escudo do Trono apos coroacao ou vitoria
TENTATIVA_H = 24             # 1 desafio a cada 24 horas
OFFLINE_SEM_PODER, OFFLINE_PERDE = 7, 14   # dias sem entrar
DESCONF_PCT, DESCONF_MIN = 0.60, 3         # Voto de Desconfianca: 60% dos ativos, minimo de votos
BENCAO_KAGE, BONUS_DOMINIO = 0.05, 0.10    # so economia (nao vale na Arena nem na Guerra)
BRT = timedelta(hours=3)     # Brasilia = UTC-3
LUTAS_GUERRA_MAX = 20
PONTOS_VITORIA, PONTOS_DERROTA, RYOS_VITORIA = 10, 2, 20


class RegraErro(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.msg, self.code = msg, code


def agora():
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def ts(dt):
    return dt.strftime(FMT)


def lt(s):
    return datetime.strptime(s, FMT) if s else None


# ------------------------------------------------------------------ tempo
def brt(dt=None):
    return (dt or agora()) - BRT


def em_janela(dt=None):
    """Guerra: sabado 20h-22h (Brasilia)."""
    b = brt(dt)
    return b.weekday() == 5 and 20 <= b.hour < 22


def semana_id(dt=None):
    y, w, _ = brt(dt).isocalendar()
    return f"{y}-W{w:02d}"


def janela(semana):
    """(inicio, fim) da janela de batalha da semana, em UTC naive."""
    y, w = int(semana[:4]), int(semana[6:])
    sab = date.fromisocalendar(y, w, 6)
    ini = datetime(sab.year, sab.month, sab.day, 20) + BRT
    return ini, ini + timedelta(hours=2)


def semana_anterior(semana):
    y, w = int(semana[:4]), int(semana[6:])
    d = date.fromisocalendar(y, w, 1) - timedelta(days=7)
    y2, w2, _ = d.isocalendar()
    return f"{y2}-W{w2:02d}"


# --------------------------------------------------------------- patentes
def patente_por_pontos(pp):
    return [n for n, m in PATENTES if pp >= m][-1]


def atualizar_patente(db, pid):
    pp = db.execute("SELECT pp_carreira FROM players WHERE id=?", (pid,)).fetchone()[0]
    db.execute("UPDATE players SET patente=? WHERE id=?", (patente_por_pontos(pp), pid))


def cronica(db, texto):
    db.execute("INSERT INTO world_chronicle(texto) VALUES (?)", (texto,))


def sannins(db, vid):
    """Os 3 melhores Jounin Elite da aldeia (por fama de carreira)."""
    return db.execute("""SELECT id, nome, classe, pp_carreira FROM players
                         WHERE village_id=? AND patente='Jounin Elite'
                         ORDER BY pp_carreira DESC, id LIMIT 3""", (vid,)).fetchall()


def e_sannin(db, vid, pid):
    return any(s["id"] == pid for s in sannins(db, vid))


# ------------------------------------------------------------------ trono
def trono(db, vid):
    return db.execute("SELECT * FROM thrones WHERE village_id=?", (vid,)).fetchone()


def dias_offline(db, pid):
    u = db.execute("SELECT ultimo_acesso FROM players WHERE id=?", (pid,)).fetchone()
    return (agora() - lt(u[0])).total_seconds() / 86400 if u else 0


def vagar(db, vid, motivo):
    t = trono(db, vid)
    if not t or not t["kage_id"]:
        return
    nome = db.execute("SELECT nome FROM players WHERE id=?", (t["kage_id"],)).fetchone()[0]
    dias = max(0, (agora() - lt(t["desde"])).days) if t["desde"] else 0
    db.execute("UPDATE reigns SET fim=? WHERE village_id=? AND kage_id=? AND fim IS NULL", (ts(agora()), vid, t["kage_id"]))
    db.execute("UPDATE hall_of_kages SET dias_reinado=? WHERE village_id=? AND numero=?", (dias, vid, t["numero"]))
    db.execute("UPDATE players SET titulo_equipado=NULL WHERE id=? AND titulo_equipado LIKE '%Kage de%'", (t["kage_id"],))
    db.execute("UPDATE thrones SET kage_id=NULL, escudo_ate=NULL WHERE village_id=?", (vid,))
    va = db.execute("SELECT nome FROM villages WHERE id=?", (vid,)).fetchone()[0]
    cronica(db, f"O trono de {va} ficou vago: {nome} deixou o cargo ({motivo}).")


def manutencao(db, vid):
    """Regras passivas, aplicadas na primeira consulta (sem relogio em segundo plano)."""
    t = trono(db, vid)
    if t and t["kage_id"] and dias_offline(db, t["kage_id"]) >= OFFLINE_PERDE:
        vagar(db, vid, f"{OFFLINE_PERDE} dias sem entrar")


def coroar(db, vid, pid, discurso=""):
    t = trono(db, vid)
    n = t["numero"] + 1
    desde = ts(agora())
    escudo = ts(agora() + timedelta(hours=ESCUDO_H))
    va = db.execute("SELECT nome FROM villages WHERE id=?", (vid,)).fetchone()[0]
    nome = db.execute("SELECT nome FROM players WHERE id=?", (pid,)).fetchone()[0]
    db.execute("UPDATE thrones SET kage_id=?, desde=?, escudo_ate=?, numero=? WHERE village_id=?", (pid, desde, escudo, n, vid))
    db.execute("INSERT INTO reigns(village_id,kage_id,inicio) VALUES (?,?,?)", (vid, pid, desde))
    db.execute("INSERT INTO hall_of_kages(village_id,player_nome,numero,discurso) VALUES (?,?,?,?)",
               (vid, nome, n, (discurso or "")[:140]))
    db.execute("UPDATE players SET titulo_equipado=? WHERE id=?", (f"{n}º Kage de {va}", pid))
    cronica(db, f"{nome} foi coroado {n}º Kage de {va}." + (f' "{discurso[:140]}"' if discurso else ""))


def pode_coroar(db, p):
    t = trono(db, p["village_id"])
    if t["kage_id"]:
        return False, "o trono já tem um Kage"
    if not e_sannin(db, p["village_id"], p["id"]):
        return False, "só um Sannin da aldeia pode assumir o trono"
    if p["pp_carreira"] < FAMA_KAGE:
        return False, f"é preciso {FAMA_KAGE} de fama de carreira"
    return True, ""


def pode_desafiar(db, p):
    vid = p["village_id"]
    t = trono(db, vid)
    if not t["kage_id"]:
        return False, "o trono está vago"
    if t["kage_id"] == p["id"]:
        return False, "você já é o Kage"
    if not e_sannin(db, vid, p["id"]):
        return False, "só um Sannin da aldeia pode desafiar o Kage"
    kpp = db.execute("SELECT pp_carreira FROM players WHERE id=?", (t["kage_id"],)).fetchone()[0]
    if p["pp_carreira"] < FAMA_KAGE:
        return False, f"é preciso {FAMA_KAGE} de fama de carreira"
    if p["pp_carreira"] < kpp * PCT_DESAFIO:
        return False, f"é preciso ter {int(PCT_DESAFIO * 100)}% da fama do Kage ({int(kpp * PCT_DESAFIO)})"
    if t["escudo_ate"] and lt(t["escudo_ate"]) > agora():
        return False, "o Kage está sob o Escudo do Trono"
    if em_janela():
        return False, "desafios não valem durante a Batalha de sábado"
    ult = db.execute("SELECT criado_em FROM throne_challenges WHERE desafiante_id=? ORDER BY id DESC LIMIT 1", (p["id"],)).fetchone()
    if ult and agora() - lt(ult[0]) < timedelta(hours=TENTATIVA_H):
        return False, "1 desafio a cada 24 horas"
    return True, ""


# -------------------------------------------------------------- playbook
class DecisorPlaybook:
    """Defesa montada (PvP assincrono): ate 3 regras SE/ENTAO sobre o bot padrao."""

    def __init__(self, regras):
        self.regras = list(regras or [])[:3]

    @staticmethod
    def _vale(se, eu, rival):
        if not se:
            return False
        if "hp_abaixo" in se and not eu.hp / eu.hp_max < float(se["hp_abaixo"]):
            return False
        if "rival_ultima" in se and (not rival.historico or rival.historico[-1] != se["rival_ultima"]):
            return False
        return True

    def __call__(self, eu, rival, bat):
        d = C.bot(eu, rival, bat)
        for r in self.regras:
            if self._vale(r.get("se") or {}, eu, rival):
                e = r.get("entao") or {}
                if e.get("postura") in C.POSTURAS:
                    d["postura"] = e["postura"]
                ok = C.REACOES + [C.CLASSE_REACAO[eu.classe][0]]
                reacoes = [x for x in (e.get("reacoes") or []) if x in ok][:2]
                if reacoes:
                    d["reacoes"] = reacoes
                if e.get("curar") and eu.classe == "Kunoichi":
                    d["curar"] = True
                break
        return d


def validar_playbook(regras):
    if not isinstance(regras, list) or len(regras) > 3:
        raise RegraErro("o Playbook aceita até 3 regras")
    out = []
    for r in regras:
        se, entao = (r or {}).get("se") or {}, (r or {}).get("entao") or {}
        if not se or not set(se) <= {"hp_abaixo", "rival_ultima"}:
            raise RegraErro("condição inválida")
        if "hp_abaixo" in se and not 0 < float(se["hp_abaixo"]) <= 1:
            raise RegraErro("hp_abaixo deve ficar entre 0 e 1")
        if "rival_ultima" in se and se["rival_ultima"] not in C.POSTURAS:
            raise RegraErro("postura inválida na condição")
        if entao.get("postura") not in C.POSTURAS + [None]:
            raise RegraErro("postura inválida")
        out.append({"se": se, "entao": {k: entao[k] for k in ("postura", "reacoes", "curar") if k in entao}})
    return out


def decisor_de(db, pid):
    row = db.execute("SELECT regras_json FROM playbooks WHERE player_id=?", (pid,)).fetchone()
    return DecisorPlaybook(json.loads(row[0]) if row else [])


def lutador_normalizado(p):
    """Arena, Trono e Guerra normalizam atributos: equipamento e Ascensao nao decidem."""
    return C.criar(p["classe"], p["nome"], p["elemento"] or "Fogo")


# --------------------------------------------------------- duelo do trono
def iniciar_desafio(db, p):
    ok, motivo = pode_desafiar(db, p)
    if not ok:
        raise RegraErro(motivo, 403)
    t = trono(db, p["village_id"])
    kage = db.execute("SELECT * FROM players WHERE id=?", (t["kage_id"],)).fetchone()
    db.execute("INSERT INTO throne_challenges(village_id,desafiante_id,kage_id) VALUES (?,?,?)", (p["village_id"], p["id"], kage["id"]))
    return kage, {"tipo": "trono", "village": p["village_id"], "kage_id": kage["id"], "bid": f"t{p['id']}-{ts(agora())}"}


def resolver_duelo(db, p, venceu, ctx):
    vid, kid = ctx["village"], ctx["kage_id"]
    t = trono(db, vid)
    if t["kage_id"] != kid:   # o trono mudou durante a luta: resultado anulado
        return {"trono": "anulado"}
    va = db.execute("SELECT nome FROM villages WHERE id=?", (vid,)).fetchone()[0]
    kn = db.execute("SELECT nome FROM players WHERE id=?", (kid,)).fetchone()[0]
    ult = db.execute("SELECT id FROM throne_challenges WHERE desafiante_id=? ORDER BY id DESC LIMIT 1", (p["id"],)).fetchone()
    if venceu:
        vagar(db, vid, "derrotado no Desafio ao Trono")
        coroar(db, vid, p["id"])
        db.execute("UPDATE throne_challenges SET vencedor_id=? WHERE id=?", (p["id"], ult[0]))
        cronica(db, f"{p['nome']} derrotou {kn} e tomou o trono de {va}.")
        return {"trono": "conquistado", "escudo_horas": ESCUDO_H}
    db.execute("UPDATE throne_challenges SET vencedor_id=? WHERE id=?", (kid, ult[0]))
    db.execute("UPDATE reigns SET desafios_vencidos=desafios_vencidos+1 WHERE village_id=? AND kage_id=? AND fim IS NULL", (vid, kid))
    cronica(db, f"{kn} defendeu o trono de {va} contra {p['nome']}.")
    return {"trono": "defendido"}


def voto_desconfianca(db, p):
    vid = p["village_id"]
    t = trono(db, vid)
    if not t["kage_id"]:
        raise RegraErro("o trono está vago")
    if t["kage_id"] == p["id"]:
        raise RegraErro("o Kage não vota contra si mesmo")
    if t["escudo_ate"] and lt(t["escudo_ate"]) > agora():
        raise RegraErro("o Kage está sob o Escudo do Trono", 403)
    r = db.execute("SELECT id FROM reigns WHERE village_id=? AND kage_id=? AND fim IS NULL", (vid, t["kage_id"])).fetchone()
    db.execute("INSERT OR IGNORE INTO no_confidence(reign_id, player_id) VALUES (?,?)", (r[0], p["id"]))
    votos = db.execute("SELECT count(*) FROM no_confidence WHERE reign_id=?", (r[0],)).fetchone()[0]
    corte = ts(agora() - timedelta(days=7))
    ativos = db.execute("SELECT count(*) FROM players WHERE village_id=? AND ultimo_acesso>=?", (vid, corte)).fetchone()[0]
    caiu = votos >= DESCONF_MIN and votos / max(1, ativos) >= DESCONF_PCT
    if caiu:
        vagar(db, vid, f"Voto de Desconfiança ({votos}/{ativos})")
    return {"votos": votos, "ativos": ativos, "necessario": f"{int(DESCONF_PCT * 100)}% (mínimo {DESCONF_MIN})", "kage_caiu": caiu}


# ------------------------------------------------------- guerra das vilas
def declaracao(db, semana, vid):
    return db.execute("SELECT * FROM war_declarations WHERE semana=? AND village_id=?", (semana, vid)).fetchone()


def aprovar_se_preciso(db, semana, vid):
    sim = db.execute("SELECT count(*) FROM war_votes WHERE semana=? AND village_id=? AND voto=1", (semana, vid)).fetchone()[0]
    if sim >= 2:
        db.execute("UPDATE war_declarations SET status='aprovada' WHERE semana=? AND village_id=?", (semana, vid))
        va = db.execute("SELECT nome FROM villages WHERE id=?", (vid,)).fetchone()[0]
        cronica(db, f"O Conselho dos Três de {va} aprovou a guerra desta semana.")
    return sim


def declarar_guerra(db, p):
    vid, sem = p["village_id"], semana_id()
    t = trono(db, vid)
    if t["kage_id"] != p["id"]:
        raise RegraErro("só o Kage declara guerra (o Conselho vota)", 403)
    if agora() >= janela(sem)[0]:
        raise RegraErro("a declaração deve ser feita antes da janela de sábado", 409)
    if declaracao(db, sem, vid):
        raise RegraErro("a guerra desta semana já foi proposta", 409)
    db.execute("INSERT INTO war_declarations(semana,village_id,proposto_por) VALUES (?,?,?)", (sem, vid, p["id"]))
    db.execute("INSERT INTO war_votes(semana,village_id,player_id,voto) VALUES (?,?,?,1)", (sem, vid, p["id"]))
    return {"semana": sem, "votos_sim": aprovar_se_preciso(db, sem, vid)}


def votar_guerra(db, p, apoiar):
    vid, sem = p["village_id"], semana_id()
    t = trono(db, vid)
    if not t["kage_id"]:
        raise RegraErro("com o trono vago o Conselho administra, mas não declara guerra", 409)
    if not e_sannin(db, vid, p["id"]) or t["kage_id"] == p["id"]:
        raise RegraErro("só os outros Sannins votam", 403)
    d = declaracao(db, sem, vid)
    if not d:
        raise RegraErro("não há proposta de guerra nesta semana", 404)
    if agora() >= janela(sem)[0]:
        raise RegraErro("votação encerrada", 409)
    db.execute("INSERT OR REPLACE INTO war_votes(semana,village_id,player_id,voto) VALUES (?,?,?,?)", (sem, vid, p["id"], 1 if apoiar else 0))
    return {"votos_sim": aprovar_se_preciso(db, sem, vid)}


def participantes(db, sem):
    return [r[0] for r in db.execute("SELECT village_id FROM war_declarations WHERE semana=? AND status='aprovada'", (sem,))]


def iniciar_luta_guerra(db, p):
    sem, vid = semana_id(), p["village_id"]
    if not em_janela():
        raise RegraErro("a Guerra acontece no sábado, das 20h às 22h (Brasília)", 409)
    parts = participantes(db, sem)
    if vid not in parts:
        raise RegraErro("sua aldeia não está em guerra esta semana", 403)
    inimigas = [v for v in parts if v != vid]
    if not inimigas:
        raise RegraErro("nenhuma aldeia inimiga declarou guerra", 409)
    if p["nivel"] < 10:
        raise RegraErro("a Guerra exige nível 10", 403)
    n = db.execute("SELECT count(*) FROM war_fights WHERE semana=? AND player_id=?", (sem, p["id"])).fetchone()[0]
    if n >= LUTAS_GUERRA_MAX:
        raise RegraErro(f"limite de {LUTAS_GUERRA_MAX} lutas por guerra", 409)
    alvo_v = random.choice(inimigas)
    cand = db.execute("SELECT * FROM players WHERE village_id=? AND nivel>=10 ORDER BY random() LIMIT 1", (alvo_v,)).fetchone()
    nome_v = db.execute("SELECT nome FROM villages WHERE id=?", (alvo_v,)).fetchone()[0]
    if cand:
        rival, dec = lutador_normalizado(cand), decisor_de(db, cand["id"])
    else:
        rival, dec = C.criar(random.choice(C.CLASSES), f"Sentinela de {nome_v}", random.choice(C.ELEMENTOS)), C.decisor_monstro
    return rival, dec, {"tipo": "guerra", "semana": sem, "alvo": alvo_v, "bid": f"g{p['id']}-{n}-{sem}"}


def registrar_luta_guerra(db, p, venceu, ctx):
    pontos = PONTOS_VITORIA if venceu else PONTOS_DERROTA
    dia = brt().strftime("%Y-%m-%d")
    db.execute("INSERT INTO war_fights(semana,player_id,village_id,venceu,pontos) VALUES (?,?,?,?,?)",
               (ctx["semana"], p["id"], p["village_id"], 1 if venceu else 0, pontos))
    db.execute("""INSERT INTO war_points(semana,village_id,player_id,pontos,dia) VALUES (?,?,?,?,?)
                  ON CONFLICT(semana,village_id,player_id,dia) DO UPDATE SET pontos=pontos+excluded.pontos""",
               (ctx["semana"], p["village_id"], p["id"], pontos, dia))
    return {"guerra_pontos": pontos, "ryos_guerra": RYOS_VITORIA if venceu else 0}


def placar(db, sem):
    nomes = {r[0]: r[1] for r in db.execute("SELECT id, nome FROM villages")}
    pts = {r[0]: r[1] for r in db.execute("SELECT village_id, sum(pontos) FROM war_points WHERE semana=? GROUP BY village_id", (sem,))}
    return sorted(({"aldeia_id": v, "aldeia": nomes[v], "pontos": pts.get(v, 0)} for v in participantes(db, sem)), key=lambda x: -x["pontos"])


def resolver_guerra(db, sem):
    """Fecha a guerra da semana depois das 22h: o placar vira dominio e Jornal."""
    if agora() < janela(sem)[1] or db.execute("SELECT 1 FROM war_resolved WHERE semana=?", (sem,)).fetchone():
        return None
    db.execute("INSERT INTO war_resolved(semana) VALUES (?)", (sem,))
    tab = placar(db, sem)
    if len(tab) < 2 or tab[0]["pontos"] == 0:
        return None
    if tab[0]["pontos"] == tab[1]["pontos"]:
        cronica(db, f"A Guerra das Vilas terminou empatada entre {tab[0]['aldeia']} e {tab[1]['aldeia']}.")
        return None
    v = tab[0]
    db.execute("INSERT OR REPLACE INTO war_dominio(village_id, ate, semana) VALUES (?,?,?)", (v["aldeia_id"], ts(agora() + timedelta(days=7)), sem))
    cronica(db, f"{v['aldeia']} venceu a Guerra das Vilas ({v['pontos']} pontos) e domina a semana.")
    return v


def resolver_pendentes(db):
    sem = semana_id()
    for s in (sem, semana_anterior(sem)):
        resolver_guerra(db, s)


def bonus_ryos(db, vid):
    """Beneficio so economico, nunca vale na Arena nem na Guerra (GDD 25)."""
    mult = 1.0
    t = trono(db, vid)
    if t and t["kage_id"] and dias_offline(db, t["kage_id"]) < OFFLINE_SEM_PODER:
        mult += BENCAO_KAGE
    d = db.execute("SELECT ate FROM war_dominio WHERE village_id=?", (vid,)).fetchone()
    if d and lt(d[0]) > agora():
        mult += BONUS_DOMINIO
    return mult


# ----------------------------------------------------------------- estado
def estado_aldeia(db, p):
    vid = p["village_id"]
    manutencao(db, vid)
    resolver_pendentes(db)
    t = trono(db, vid)
    sem = semana_id()
    va = db.execute("SELECT nome FROM villages WHERE id=?", (vid,)).fetchone()[0]
    kage = None
    if t["kage_id"]:
        k = db.execute("SELECT nome, classe, pp_carreira FROM players WHERE id=?", (t["kage_id"],)).fetchone()
        off = dias_offline(db, t["kage_id"])
        kage = {"id": t["kage_id"], "nome": k["nome"], "classe": k["classe"], "fama": k["pp_carreira"], "numero": t["numero"],
                "desde": t["desde"], "escudo_ate": t["escudo_ate"], "escudo_ativo": bool(t["escudo_ate"] and lt(t["escudo_ate"]) > agora()),
                "dias_offline": round(off, 1), "poderes_suspensos": off >= OFFLINE_SEM_PODER}
    d = declaracao(db, sem, vid)
    votos = db.execute("SELECT count(*) FROM war_votes WHERE semana=? AND village_id=? AND voto=1", (sem, vid)).fetchone()[0] if d else 0
    ini, fim = janela(sem)
    des, mdes = pode_desafiar(db, p)
    cor, mcor = pode_coroar(db, p)
    return {
        "aldeia": va, "trono_vago": not t["kage_id"], "kage": kage,
        "conselho": [{"id": s["id"], "nome": s["nome"], "classe": s["classe"], "fama": s["pp_carreira"], "e_kage": s["id"] == t["kage_id"]} for s in sannins(db, vid)],
        "eu": {"patente": p["patente"], "fama": p["pp_carreira"], "sou_sannin": e_sannin(db, vid, p["id"]), "sou_kage": t["kage_id"] == p["id"],
               "pode_desafiar": des, "motivo_desafio": mdes, "pode_coroar": cor, "motivo_coroar": mcor,
               "fama_para_kage": FAMA_KAGE},
        "guerra": {"semana": sem, "janela_inicio": ts(ini), "janela_fim": ts(fim), "em_janela": em_janela(),
                   "declaracao": d["status"] if d else None, "votos_sim": votos, "placar": placar(db, sem),
                   "lutas_minhas": db.execute("SELECT count(*) FROM war_fights WHERE semana=? AND player_id=?", (sem, p["id"])).fetchone()[0],
                   "lutas_max": LUTAS_GUERRA_MAX,
                   "dominio_ate": (db.execute("SELECT ate FROM war_dominio WHERE village_id=?", (vid,)).fetchone() or [None])[0]},
        "bonus_ryos": round(bonus_ryos(db, vid), 2),
    }
