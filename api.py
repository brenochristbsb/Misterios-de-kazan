"""api.py - servidor autoritativo do Shadow Clan RPG (GDD v1.2, seções 8, 9, 11, 14, 20).

O cliente só exibe: combate, drops, energia e moeda são decididos AQUI.
Rodar:  SHADOW_DEV=1 python api.py      (dev, sem Telegram)
        BOT_TOKEN=... python api.py     (produção: valida initData do Telegram)
"""
from __future__ import annotations
import hashlib
import hmac
import json
import os
import pickle
import random
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl

from flask import Flask, g, jsonify, request, send_from_directory

import combate_reativo as C
import dados as D
import governo as G
import jutsus as J

AQUI = os.path.dirname(os.path.abspath(__file__))
ENERGIA_MAX, ENERGIA_MIN_REGEN = 25, 6          # 1 ponto a cada 6 min (GDD 28)
CUSTO_EXPLORAR = 1
TAXA_LISTAGEM, TAXA_VENDA = 10, 0.05            # GDD 8.3
SPAWN = [("facil", 55), ("medio", 33), ("dificil", 11)]  # boss: 1% base + pity
TIER_MULT = {"facil": .8, "medio": 1.0, "dificil": 1.25, "boss": 1.6}
TIER_RECOMP = {"facil": 1.0, "medio": 1.6, "dificil": 2.5, "boss": 6.0}
HOSPITAL = {"basico": 10, "completo": 25, "premium": 60}   # Ryos × nível (GDD 28)
SLOTS = ["cabeca", "amuleto", "peitoral", "pernas", "luvas", "anel_dir", "anel_esq", "arma", "pergaminho", "pet"]
FMT = "%Y-%m-%d %H:%M:%S"


def agora():
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def ts(dt):
    return dt.strftime(FMT)


class Erro(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.msg, self.code = msg, code


# ---------------------------------------------------------------- banco
def conectar(path):
    db = sqlite3.connect(path, isolation_level=None, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA journal_mode = WAL")
    return db


def iniciar_banco(path):
    """Cria o banco SE não existir; nunca apaga nada (regra de ouro, GDD 0)."""
    db = conectar(path)
    with open(os.path.join(AQUI, "schema.sql"), encoding="utf-8") as f:
        db.executescript(f.read())
    if not db.execute("SELECT 1 FROM villages LIMIT 1").fetchone():
        for nome, sig, pedra in D.ALDEIAS:
            cur = db.execute("INSERT INTO villages(nome, significado, pedra_selo) VALUES (?,?,?)", (nome, sig, pedra))
            for cn, esp in D.CLAS[nome]:
                db.execute("INSERT INTO clans(village_id, nome, especialidade) VALUES (?,?,?)", (cur.lastrowid, cn, esp))
            db.execute("INSERT INTO thrones(village_id) VALUES (?)", (cur.lastrowid,))
        for it in D.ITENS:
            db.execute("""INSERT INTO items(chave,nome,slot,raridade,classe_restrita,dano,preco_npc,empilhavel,bonus_json)
                          VALUES (?,?,?,?,?,?,?,?,?)""", (*it, D.BONUS.get(it[0], "{}")))
    J.semear(db)
    db.close()


@contextmanager
def tx(db):
    db.execute("BEGIN IMMEDIATE")
    try:
        yield
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise


# ------------------------------------------------------------ autenticação
def validar_init_data(init_data, token, max_idade=86400):
    """Valida o initData do Telegram Mini App (HMAC). Devolve o dict do usuário ou None."""
    pares = dict(parse_qsl(init_data, keep_blank_values=True))
    h = pares.pop("hash", None)
    if not h:
        return None
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(pares.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest(), h):
        return None
    if time.time() - int(pares.get("auth_date", 0)) > max_idade:
        return None
    try:
        return json.loads(pares["user"])
    except (KeyError, ValueError):
        return None


# -------------------------------------------------------------- economia
def mover(db, pid, moeda, delta, motivo, idem=None):
    """Ganho/gasto atômico com ledger idempotente. Chamar dentro de tx()."""
    col = {"ryos": "ryos", "ouro_ninja": "ouro_ninja", "selos_arena": "selos_arena", "selos_torre": "selos_torre"}[moeda]
    idem = idem or uuid.uuid4().hex
    if db.execute("SELECT 1 FROM currency_ledger WHERE idem_key=?", (idem,)).fetchone():
        return False  # já processada
    saldo = db.execute(f"SELECT {col} FROM players WHERE id=?", (pid,)).fetchone()[0] + delta
    if saldo < 0:
        raise Erro(f"{moeda} insuficiente", 402)
    db.execute(f"UPDATE players SET {col}=? WHERE id=?", (saldo, pid))
    db.execute("INSERT INTO currency_ledger(player_id,moeda,delta,saldo_apos,motivo,idem_key) VALUES (?,?,?,?,?,?)",
               (pid, moeda, delta, saldo, motivo, idem))
    return True


def energia_atual(p):
    """Regeneração lazy: 1 ponto a cada 6 minutos. Devolve (energia, novo_marco_de_tempo)."""
    base = datetime.strptime(p["energia_em"], FMT)
    if p["energia"] >= ENERGIA_MAX:
        return ENERGIA_MAX, ts(agora())
    ganhos = int((agora() - base).total_seconds() // (ENERGIA_MIN_REGEN * 60))
    nova = min(ENERGIA_MAX, p["energia"] + ganhos)
    marco = ts(agora()) if nova >= ENERGIA_MAX else ts(base + timedelta(minutes=ganhos * ENERGIA_MIN_REGEN))
    return nova, marco


def xp_proximo(nivel):
    return int(50 * nivel ** 1.5)


def dar_xp(db, pid, xp):
    p = db.execute("SELECT nivel, xp FROM players WHERE id=?", (pid,)).fetchone()
    nivel, total, subiu = p["nivel"], p["xp"] + xp, 0
    while nivel < 100 and total >= xp_proximo(nivel):
        total -= xp_proximo(nivel)
        nivel += 1
        subiu += 1
    if nivel >= 100:
        total = 0
    db.execute("UPDATE players SET nivel=?, xp=?, pontos_livres=pontos_livres+? WHERE id=?", (nivel, total, subiu * 5, pid))
    return subiu


# --------------------------------------------------------------- jogador
def jogador(db, pid):
    p = db.execute("SELECT * FROM players WHERE id=?", (pid,)).fetchone()
    if not p:
        raise Erro("jogador não encontrado", 404)
    return p


def equipados(db, pid):
    return db.execute("""SELECT i.id, i.slot, i.qualidade, it.chave, it.nome, it.dano, it.bonus_json
                         FROM inventory i JOIN items it ON it.id=i.item_id
                         WHERE i.player_id=? AND i.local='equipado'""", (pid,)).fetchall()


QUALIDADE_MULT = {"Comum": 1.0, "Refinado": 1.05, "Obra-Prima": 1.10, "Perfeito": 1.20}


def montar_lutador(db, p):
    attrs = {"STR": p["str"], "AGI": p["agi"], "INT": p["int"], "DEX": p["dex"], "VIG": p["vig"]}
    arma = 0
    for e in equipados(db, p["id"]):
        m = QUALIDADE_MULT[e["qualidade"]]
        arma += int(e["dano"] * m)
        for k, v in json.loads(e["bonus_json"]).items():
            attrs[k] += int(v * m)
    if p["buff_ataque_ate"] and datetime.strptime(p["buff_ataque_ate"], FMT) > agora():
        k = "STR" if p["classe"] == "Taijutsu" else "INT"
        attrs[k] = int(attrs[k] * 1.2)
    l = C.Lutador(p["nome"], p["classe"], p["elemento"], attrs, arma=arma)
    J.aplicar_build(db, l, p)
    if p["hp_atual"] is not None:
        l.hp = min(l.hp_max, max(1.0, p["hp_atual"]))
    return l


def perfil_json(db, p):
    en, _ = energia_atual(p)
    l = montar_lutador(db, p)
    return {
        "id": p["id"], "nome": p["nome"], "classe": p["classe"], "elemento": p["elemento"],
        "nivel": p["nivel"], "xp": p["xp"], "xp_falta": xp_proximo(p["nivel"]) - p["xp"], "patente": p["patente"],
        "fama": p["fama"], "ryos": p["ryos"], "ouro_ninja": p["ouro_ninja"], "energia": en, "energia_max": ENERGIA_MAX,
        "hp": int(l.hp), "hp_max": int(l.hp_max), "chakra_max": int(l.chakra_max),
        "atributos": {"STR": p["str"], "AGI": p["agi"], "INT": p["int"], "DEX": p["dex"], "VIG": p["vig"]},
        "pontos_livres": p["pontos_livres"], "aldeia": db.execute("SELECT nome FROM villages WHERE id=?", (p["village_id"],)).fetchone()[0],
        "titulo": p["titulo_equipado"],
    }


# --------------------------------------------------------------- monstros
def gerar_monstro(nivel, tier, mapa):
    classe = random.choice(C.CLASSES)
    total = (35 + 5 * (nivel - 1)) * TIER_MULT[tier]
    base = C.PRESETS[classe]
    k = total / 150
    attrs = {a: max(1, round(v * k)) for a, v in base.items()}
    m = C.Lutador(("Chefe do Mapa %d" % mapa) if tier == "boss" else f"{classe} errante",
                  classe, random.choice(C.ELEMENTOS), attrs, arma=int(8 + nivel * 0.6 * TIER_MULT[tier]))
    return m


def sortear_tier(db, pid, mapa):
    row = db.execute("SELECT pontos FROM map_threat WHERE player_id=? AND mapa=?", (pid, mapa)).fetchone()
    pontos = row["pontos"] if row else 0
    chance_boss = 1 + pontos // 10        # +1% a cada 10 pontos de ameaça
    if pontos >= 100 or random.random() * 100 < chance_boss:
        db.execute("INSERT INTO map_threat(player_id,mapa,pontos) VALUES (?,?,0) ON CONFLICT(player_id,mapa) DO UPDATE SET pontos=0", (pid, mapa))
        return "boss"
    db.execute("INSERT INTO map_threat(player_id,mapa,pontos) VALUES (?,?,1) ON CONFLICT(player_id,mapa) DO UPDATE SET pontos=min(100,pontos+1)", (pid, mapa))
    return random.choices([t for t, _ in SPAWN], [w for _, w in SPAWN])[0]


def sortear_drops(db, p):
    """Dois rolos (GDD 8.4): materiais e equipamentos."""
    drops = []
    r = random.random() * 100
    if r < 60:
        drops.append(("material", random.choice(D.MATERIAIS_BASICOS), random.randint(1, 3)))
    elif r < 70:
        drops.append(("material", random.choice(D.MATERIAIS_RAROS), 1))
    r = random.random() * 100
    if r >= 75:
        if r < 90:
            drops.append(("material", "fragmento_mapa", 1))
        else:
            raridade = "comum" if r < 98 else ("incomum" if r < 99.8 else "raro")
            item = db.execute("""SELECT * FROM items WHERE slot IS NOT NULL AND raridade=?
                                 AND (classe_restrita IS NULL OR classe_restrita=?) ORDER BY random() LIMIT 1""",
                              (raridade, p["classe"])).fetchone()
            if item:
                drops.append(("item", item["chave"], 1))
    return drops


def entregar_drops(db, p, drops):
    entregues = []
    for tipo, chave, qtd in drops:
        if tipo == "material":
            db.execute("""INSERT INTO materials(player_id,chave,quantidade) VALUES (?,?,?)
                          ON CONFLICT(player_id,chave) DO UPDATE SET quantidade=quantidade+?""", (p["id"], chave, qtd, qtd))
            entregues.append({"chave": chave, "qtd": qtd})
        else:
            iid = db.execute("SELECT id FROM items WHERE chave=?", (chave,)).fetchone()["id"]
            try:
                db.execute("INSERT INTO inventory(player_id,item_id) VALUES (?,?)", (p["id"], iid))
                entregues.append({"chave": chave, "qtd": 1})
            except sqlite3.DatabaseError:
                entregues.append({"chave": chave, "qtd": 0, "aviso": "mochila cheia"})
    return entregues


# ---------------------------------------------------------------- combate
def estado_combate(bat, linhas):
    a, b = bat.ls
    def lut(l):
        return {"nome": l.nome, "classe": l.classe, "hp": max(0, int(l.hp)), "hp_max": int(l.hp_max),
                "chakra": int(l.chakra), "furia": l.furia, "portoes": l.portoes,
                "marionete": int(l.marionete), "veneno": l.veneno, "status": list(l.status),
                "historico": l.historico[-3:], "selos": sorted(l.selos), "supremo": l.supremo,
                "jutsus": [{"chave": k, "nome": j["nome"], "custo": j["custo"], "dano": j["dano"], "cd": l.cds.get(k, 0)} for k, j in l.jutsus.items()]}
    return {"rodada": bat.rodada, "ambiente": bat.ambiente, "terminou": bat.terminou(), "jogador": lut(a), "rival": lut(b),
            "efeitos": list(bat.efeitos), "anuncio": D.ANUNCIO_BOSS.get(bat.plano_boss) if bat.plano_boss else None,
            "log": linhas}


def iniciar_combate(db, p, rival, tipo, ambiente, ctx, jogador_l=None, decisor_rival=None):
    if db.execute("SELECT 1 FROM active_battles WHERE player_id=?", (p["id"],)).fetchone():
        raise Erro("você já está em uma luta", 409)
    bat = C.Batalha(jogador_l or montar_lutador(db, p), rival, ambiente, boss=(tipo == "boss"), decisores=[C.escolha_jogador, decisor_rival or C.decisor_monstro])
    bat.plano_boss = random.choice(C.POSTURAS) if tipo == "boss" else None
    bat.abrir()
    bid = uuid.uuid4().hex
    db.execute("INSERT INTO active_battles(id,player_id,tipo,estado,contexto_json) VALUES (?,?,?,?,?)",
               (bid, p["id"], tipo, pickle.dumps(bat), json.dumps(ctx)))
    return estado_combate(bat, list(bat.n.linhas))


def validar_decisao(p, corpo):
    postura = corpo.get("postura")
    if postura not in C.POSTURAS:
        raise Erro("postura inválida")
    nome_cls = C.CLASSE_REACAO[p["classe"]][0]
    reacoes = corpo.get("reacoes") or []
    if not isinstance(reacoes, list) or len(reacoes) > 2 or any(r not in C.REACOES + [nome_cls] for r in reacoes):
        raise Erro("reações inválidas (máximo 2)")
    return {"postura": postura, "reacoes": reacoes, "portao": bool(corpo.get("portao")), "curar": bool(corpo.get("curar")),
            "jutsu": str(corpo.get("jutsu") or "")}


def liquidar(db, p, bat, tipo, ctx):
    """Aplica o resultado da luta: HP, recompensas, log. Chamar dentro de tx()."""
    jogador_l, rival = bat.ls
    venceu = bat.vencedor is jogador_l
    recompensa = {"venceu": venceu}
    if tipo not in ("trono", "guerra"):   # duelos normalizados nao mexem na vida real
        hp_final = max(1.0, jogador_l.hp) if venceu else max(1.0, jogador_l.hp_max * 0.1)
        db.execute("UPDATE players SET hp_atual=? WHERE id=?", (hp_final, p["id"]))
    if tipo in ("pve", "boss") and venceu:
        f = TIER_RECOMP[ctx["tier"]]
        xp, ryos = int((10 + p["nivel"] * 2) * f), int((8 + p["nivel"] * 1.5) * f * G.bonus_ryos(db, p["village_id"]))
        subiu = dar_xp(db, p["id"], xp)
        mover(db, p["id"], "ryos", ryos, f"luta {ctx['tier']}", f"luta:{ctx['bid']}:ryos")
        fama = 15 if ctx["tier"] == "boss" else 0
        if fama:
            db.execute("UPDATE players SET fama=fama+?, pp_carreira=pp_carreira+? WHERE id=?", (fama, fama, p["id"]))
            G.atualizar_patente(db, p["id"])
        recompensa.update(xp=xp, ryos=ryos, fama=fama, subiu=subiu,
                          drops=entregar_drops(db, p, sortear_drops(db, p)))
    if tipo == "trono":
        recompensa.update(G.resolver_duelo(db, p, venceu, ctx))
    elif tipo == "guerra":
        recompensa.update(G.registrar_luta_guerra(db, p, venceu, ctx))
        if venceu:
            mover(db, p["id"], "ryos", G.RYOS_VITORIA, "guerra das vilas", f"guerra:{ctx['bid']}")
            db.execute("UPDATE players SET fama=fama+5, pp_carreira=pp_carreira+5 WHERE id=?", (p["id"],))
            G.atualizar_patente(db, p["id"])
    db.execute("INSERT INTO battle_logs(tipo,player_id,rival,ambiente,vencedor,rodadas,log_json,cronica) VALUES (?,?,?,?,?,?,?,?)",
               (tipo, p["id"], rival.nome, bat.ambiente, bat.vencedor.nome if bat.vencedor else None, bat.rodada,
                json.dumps(bat.n.linhas, ensure_ascii=False), "\n".join(bat.n.cronica(bat.vencedor, bat.rodada))))
    db.execute("DELETE FROM active_battles WHERE player_id=?", (p["id"],))
    return recompensa


# ------------------------------------------------------------------- app
def create_app(db_path=None, dev=None, bot_token=None):
    app = Flask(__name__, static_folder=None)
    app.config["DB"] = db_path or os.environ.get("SHADOW_DB", os.path.join(AQUI, "shadow.db"))
    app.config["DEV"] = dev if dev is not None else os.environ.get("SHADOW_DEV") == "1"
    app.config["TOKEN"] = bot_token or os.environ.get("BOT_TOKEN", "")
    app.config["SEGREDO"] = os.environ.get("SHADOW_SECRET", "troque-em-producao").encode()
    iniciar_banco(app.config["DB"])
    limites = {}

    def db():
        if "db" not in g:
            g.db = conectar(app.config["DB"])
        return g.db

    @app.teardown_appcontext
    def fechar(_):
        d = g.pop("db", None)
        if d:
            d.close()

    @app.errorhandler(Erro)
    def erro(e):
        return jsonify(erro=e.msg), e.code

    @app.errorhandler(sqlite3.IntegrityError)
    def integridade(e):
        return jsonify(erro=f"operação inválida: {e}"), 409

    def tg_id():
        if app.config["DEV"] and request.headers.get("X-Dev-User"):
            uid = int(request.headers["X-Dev-User"])
        else:
            u = validar_init_data(request.headers.get("X-Telegram-Init-Data", ""), app.config["TOKEN"])
            if not u:
                raise Erro("não autenticado", 401)
            uid = int(u["id"])
        janela = [t for t in limites.get(uid, []) if time.time() - t < 10]
        if len(janela) >= 40:
            raise Erro("muitas requisições", 429)
        limites[uid] = janela + [time.time()]
        return uid

    def atual():
        p = db().execute("SELECT * FROM players WHERE telegram_id=?", (tg_id(),)).fetchone()
        if not p:
            raise Erro("crie seu personagem primeiro", 404)
        db().execute("UPDATE players SET ultimo_acesso=datetime('now') WHERE id=?", (p["id"],))
        return p

    def em_luta(pid):
        return db().execute("SELECT 1 FROM active_battles WHERE player_id=?", (pid,)).fetchone() is not None

    @app.get("/")
    def index():
        return send_from_directory(AQUI, "index.html")

    # --- criação
    def token_resp(qi, elem):
        return hmac.new(app.config["SEGREDO"], f"{qi}:{elem}".encode(), hashlib.sha256).hexdigest()[:10]

    @app.get("/api/questionario")
    def questionario():
        out = []
        for qi, (txt, resp) in enumerate(D.QUESTIONARIO):
            ops = [{"token": token_resp(qi, e), "texto": t} for e, t in resp.items()]
            random.shuffle(ops)  # ordem embaralhada a cada vez (GDD 4.1)
            out.append({"id": qi, "pergunta": txt, "opcoes": ops})
        return jsonify(perguntas=out, aldeias=[dict(r) for r in db().execute(
            "SELECT v.id, v.nome, v.significado, (SELECT count(*) FROM players WHERE village_id=v.id) AS moradores FROM villages v")])

    @app.post("/api/personagem")
    def criar_personagem():
        uid, c = tg_id(), request.get_json(force=True)
        if db().execute("SELECT 1 FROM players WHERE telegram_id=?", (uid,)).fetchone():
            raise Erro("você já tem um personagem", 409)
        nome, sexo, classe = (c.get("nome") or "").strip(), c.get("sexo"), c.get("classe")
        if classe not in C.CLASSES:
            raise Erro("classe inválida")
        elemento = None
        if classe != "Taijutsu":
            resp = c.get("respostas") or []
            if len(resp) != len(D.QUESTIONARIO):
                raise Erro("responda as 5 perguntas")
            pontos = {e: 0 for e in C.ELEMENTOS}
            for qi, tk in enumerate(resp):
                for e in C.ELEMENTOS:
                    if hmac.compare_digest(token_resp(qi, e), str(tk)):
                        pontos[e] += 1
            if sum(pontos.values()) != len(D.QUESTIONARIO):
                raise Erro("respostas inválidas")
            top = max(pontos.values())
            empatados = [e for e, v in pontos.items() if v == top]
            if len(empatados) > 1:  # empate: vale a resposta da pergunta 5
                ultimo = [e for e in C.ELEMENTOS if hmac.compare_digest(token_resp(4, e), str(resp[4]))][0]
                elemento = ultimo
            else:
                elemento = empatados[0]
        vid = c.get("aldeia_id")
        d = db()
        if vid is None:  # o jogo sugere a aldeia menos populosa
            vid = d.execute("""SELECT v.id FROM villages v LEFT JOIN players p ON p.village_id=v.id
                               GROUP BY v.id ORDER BY count(p.id), v.id LIMIT 1""").fetchone()[0]
        ini = D.INICIAL[classe]
        with tx(d):
            cur = d.execute("""INSERT INTO players(telegram_id,nome,sexo,classe,elemento,village_id,str,agi,int,dex,vig,ryos)
                               VALUES (?,?,?,?,?,?,?,?,?,?,?,100)""",
                            (uid, nome, sexo, classe, elemento, vid, ini["str"], ini["agi"], ini["int"], ini["dex"], ini["vig"]))
            pid = cur.lastrowid
            d.execute("INSERT INTO currency_ledger(player_id,moeda,delta,saldo_apos,motivo,idem_key) VALUES (?,?,?,?,?,?)",
                      (pid, "ryos", 100, 100, "bônus inicial", f"inicio:{pid}"))
            arma = d.execute("SELECT id FROM items WHERE classe_restrita=? AND raridade='comum' AND slot='arma'", (classe,)).fetchone()
            d.execute("INSERT INTO inventory(player_id,item_id,local,slot) VALUES (?,?,'equipado','arma')", (pid, arma["id"]))
            if classe == "Marionetista":
                lut = montar_lutador(d, jogador(d, pid))
                d.execute("INSERT INTO marionettes(player_id,hp,hp_max) VALUES (?,?,?)", (pid, lut.marionete_max, lut.marionete_max))
        return jsonify(perfil_json(d, jogador(d, pid))), 201

    # --- perfil
    @app.get("/api/perfil")
    def perfil():
        p = atual()
        en, marco = energia_atual(p)
        if en != p["energia"]:
            db().execute("UPDATE players SET energia=?, energia_em=? WHERE id=?", (en, marco, p["id"]))
            p = jogador(db(), p["id"])
        out = perfil_json(db(), p)
        out["em_luta"] = em_luta(p["id"])
        out["equipados"] = [dict(nome=e["nome"], slot=e["slot"], id=e["id"]) for e in equipados(db(), p["id"])]
        return jsonify(out)

    @app.post("/api/atributos")
    def atributos():
        p, c = atual(), request.get_json(force=True)
        pts = {k: int(v) for k, v in (c.get("pontos") or {}).items()}
        if any(k not in ("str", "agi", "int", "dex", "vig") or v < 0 for k, v in pts.items()) or sum(pts.values()) > p["pontos_livres"]:
            raise Erro("pontos inválidos")
        with tx(db()):
            for k, v in pts.items():
                db().execute(f"UPDATE players SET {k}={k}+? WHERE id=?", (v, p["id"]))
            db().execute("UPDATE players SET pontos_livres=pontos_livres-? WHERE id=?", (sum(pts.values()), p["id"]))
        return jsonify(perfil_json(db(), jogador(db(), p["id"])))

    # --- mochila e equipamento
    @app.get("/api/mochila")
    def mochila():
        p = atual()
        itens = db().execute("""SELECT i.id, it.nome, it.chave, it.slot, it.raridade, it.dano, i.qualidade, i.vinculado, i.local, i.slot AS equip_slot,
                                (SELECT 1 FROM market_listings m WHERE m.inventory_id=i.id AND m.status='ativo') AS anunciado
                                FROM inventory i JOIN items it ON it.id=i.item_id WHERE i.player_id=? ORDER BY i.local DESC, i.id""", (p["id"],)).fetchall()
        mats = db().execute("SELECT chave, quantidade FROM materials WHERE player_id=? AND quantidade>0", (p["id"],)).fetchall()
        return jsonify(itens=[dict(i) for i in itens], materiais=[dict(m) for m in mats],
                       capacidade=30 + p["mochila_extra"])

    @app.post("/api/equipar")
    def equipar():
        p, c = atual(), request.get_json(force=True)
        if em_luta(p["id"]):
            raise Erro("não é possível trocar equipamento em luta", 409)
        d = db()
        item = d.execute("""SELECT i.id, i.local, it.slot, it.classe_restrita FROM inventory i JOIN items it ON it.id=i.item_id
                            WHERE i.id=? AND i.player_id=?""", (c.get("inventory_id"), p["id"])).fetchone()
        if not item or item["local"] != "mochila" or not item["slot"]:
            raise Erro("item não pode ser equipado")
        if item["classe_restrita"] and item["classe_restrita"] != p["classe"]:
            raise Erro(f"item exclusivo de {item['classe_restrita']}", 403)
        slot = c.get("slot") or item["slot"]
        if slot not in SLOTS or (slot != item["slot"] and not (item["slot"] in ("anel_dir", "anel_esq") and slot in ("anel_dir", "anel_esq"))):
            raise Erro("slot inválido")
        with tx(d):
            atual_eq = d.execute("SELECT id FROM inventory WHERE player_id=? AND local='equipado' AND slot=?", (p["id"], slot)).fetchone()
            if atual_eq:
                d.execute("UPDATE inventory SET local='mochila', slot=NULL WHERE id=?", (atual_eq["id"],))
            d.execute("UPDATE inventory SET local='equipado', slot=? WHERE id=?", (slot, item["id"]))
        return jsonify(ok=True)

    @app.post("/api/desequipar")
    def desequipar():
        p, c = atual(), request.get_json(force=True)
        if em_luta(p["id"]):
            raise Erro("não é possível trocar equipamento em luta", 409)
        with tx(db()):
            db().execute("UPDATE inventory SET local='mochila', slot=NULL WHERE player_id=? AND local='equipado' AND slot=?", (p["id"], c.get("slot")))
        return jsonify(ok=True)

    # --- exploração
    @app.post("/api/explorar")
    def explorar():
        p, c = atual(), request.get_json(force=True)
        d, mapa = db(), int(c.get("mapa", 1))
        if not 1 <= mapa <= 10 or p["nivel"] < (mapa - 1) * 10:
            raise Erro("mapa bloqueado para o seu nível", 403)
        if em_luta(p["id"]):
            raise Erro("termine a luta atual", 409)
        en, marco = energia_atual(p)
        if en < CUSTO_EXPLORAR:
            raise Erro("sem energia", 409)
        with tx(d):
            d.execute("UPDATE players SET energia=?, energia_em=? WHERE id=?",
                      (en - CUSTO_EXPLORAR, marco if en < ENERGIA_MAX else ts(agora()), p["id"]))
            tier = sortear_tier(d, p["id"], mapa)
            nivel_m = max(1, (mapa - 1) * 10 + 5)
            rival = gerar_monstro(max(p["nivel"], nivel_m - 3), tier, mapa)
            bid = uuid.uuid4().hex
            p2 = jogador(d, p["id"])
            ambiente = random.choice(list(C.AMBIENTES))
            estado = iniciar_combate(d, p2, rival, "boss" if tier == "boss" else "pve", ambiente, {"tier": tier, "mapa": mapa, "bid": bid})
        return jsonify(tier=tier, combate=estado)

    @app.get("/api/combate")
    def combate_atual():
        p = atual()
        row = db().execute("SELECT estado FROM active_battles WHERE player_id=?", (p["id"],)).fetchone()
        if not row:
            raise Erro("nenhuma luta ativa", 404)
        bat = pickle.loads(row["estado"])
        return jsonify(estado_combate(bat, list(bat.n.linhas)))

    @app.post("/api/combate/turno")
    def turno():
        p, c = atual(), request.get_json(force=True)
        d = db()
        dec = validar_decisao(p, c)
        with tx(d):
            row = d.execute("SELECT estado, tipo, contexto_json FROM active_battles WHERE player_id=?", (p["id"],)).fetchone()
            if not row:
                raise Erro("nenhuma luta ativa", 404)
            bat, ctx = pickle.loads(row["estado"]), json.loads(row["contexto_json"])
            antes = len(bat.n.linhas)
            bat.escolha = dec
            bat.passo()
            if bat.plano_boss and not bat.terminou():
                bat.plano_boss = random.choice(C.POSTURAS)  # o chefe anuncia o golpe do próximo turno
            linhas = bat.n.linhas[antes:]
            est = estado_combate(bat, linhas)
            if bat.terminou():
                bat.encerrar()
                est["log"] = bat.n.linhas[antes:]
                est["recompensa"] = liquidar(d, jogador(d, p["id"]), bat, row["tipo"], ctx)
                est["cronica"] = bat.n.cronica(bat.vencedor, bat.rodada)
            else:
                d.execute("UPDATE active_battles SET estado=? WHERE player_id=?", (pickle.dumps(bat), p["id"]))
        return jsonify(est)

    @app.post("/api/combate/fugir")
    def fugir():
        p = atual()
        with tx(db()):
            row = db().execute("SELECT tipo FROM active_battles WHERE player_id=?", (p["id"],)).fetchone()
            if not row:
                raise Erro("nenhuma luta ativa", 404)
            db().execute("DELETE FROM active_battles WHERE player_id=?", (p["id"],))
        return jsonify(ok=True)

    @app.post("/api/treino")
    def treino():
        p, c = atual(), request.get_json(force=True)
        classe, amb = c.get("classe", "Taijutsu"), c.get("ambiente", "Floresta")
        if classe not in C.CLASSES or amb not in C.AMBIENTES:
            raise Erro("classe ou ambiente inválido")
        if em_luta(p["id"]):
            raise Erro("termine a luta atual", 409)
        boneco = C.criar(classe, f"Boneco de {classe}", random.choice(C.ELEMENTOS))
        with tx(db()):
            est = iniciar_combate(db(), p, boneco, "treino", amb, {"tier": "treino", "bid": uuid.uuid4().hex})
        return jsonify(combate=est)

    # --- hospital
    @app.post("/api/hospital")
    def hospital():
        p, c = atual(), request.get_json(force=True)
        tipo, d = c.get("tipo", "gratis"), db()
        if em_luta(p["id"]):
            raise Erro("hospital bloqueado durante lutas", 409)
        lut = montar_lutador(d, p)
        perdido = lut.hp_max - lut.hp
        with tx(d):
            if tipo == "gratis":
                espera = 10 * (0.5 if p["classe"] == "Kunoichi" else 1.0)
                if p["hospital_livre_em"] and datetime.strptime(p["hospital_livre_em"], FMT) > agora():
                    raise Erro("atendimento gratuito em espera", 409)
                novo = min(lut.hp_max, lut.hp + perdido * 0.5)
                d.execute("UPDATE players SET hp_atual=?, hospital_livre_em=? WHERE id=?",
                          (novo, ts(agora() + timedelta(minutes=espera)), p["id"]))
            elif tipo in HOSPITAL:
                custo = int(HOSPITAL[tipo] * p["nivel"] * (0.7 if p["classe"] == "Kunoichi" else 1.0))
                mover(d, p["id"], "ryos", -custo, f"hospital {tipo}")
                novo = min(lut.hp_max, lut.hp + perdido * 0.5) if tipo == "basico" else lut.hp_max
                d.execute("UPDATE players SET hp_atual=? WHERE id=?", (novo, p["id"]))
                if tipo == "premium":
                    d.execute("UPDATE players SET buff_ataque_ate=? WHERE id=?", (ts(agora() + timedelta(minutes=15)), p["id"]))
            else:
                raise Erro("tratamento inválido")
        return jsonify(perfil_json(d, jogador(d, p["id"])))

    # --- mercado P2P
    @app.get("/api/mercado")
    def mercado():
        atual()
        rows = db().execute("""SELECT m.id, m.preco, it.nome, it.raridade, it.dano, i.qualidade, pl.nome AS vendedor, m.seller_id
                               FROM market_listings m JOIN inventory i ON i.id=m.inventory_id JOIN items it ON it.id=i.item_id
                               JOIN players pl ON pl.id=m.seller_id WHERE m.status='ativo' ORDER BY m.id DESC LIMIT 100""").fetchall()
        return jsonify(anuncios=[dict(r) for r in rows], taxa_listagem=TAXA_LISTAGEM, taxa_venda=TAXA_VENDA)

    @app.post("/api/mercado/anunciar")
    def anunciar():
        p, c = atual(), request.get_json(force=True)
        preco = int(c.get("preco", 0))
        if preco <= 0:
            raise Erro("preço inválido")
        with tx(db()):
            mover(db(), p["id"], "ryos", -TAXA_LISTAGEM, "taxa de listagem")
            db().execute("INSERT INTO market_listings(seller_id,inventory_id,preco) VALUES (?,?,?)", (p["id"], c.get("inventory_id"), preco))
        return jsonify(ok=True)

    @app.post("/api/mercado/comprar")
    def comprar():
        p, c = atual(), request.get_json(force=True)
        d = db()
        with tx(d):
            l = d.execute("SELECT * FROM market_listings WHERE id=? AND status='ativo'", (c.get("listing_id"),)).fetchone()
            if not l:
                raise Erro("anúncio indisponível", 404)
            if l["seller_id"] == p["id"]:
                raise Erro("não compre o próprio item", 409)
            mover(d, p["id"], "ryos", -l["preco"], "compra no mercado", f"compra:{l['id']}:b")
            taxa = int(l["preco"] * TAXA_VENDA)
            mover(d, l["seller_id"], "ryos", l["preco"] - taxa, "venda no mercado", f"compra:{l['id']}:s")
            d.execute("UPDATE inventory SET player_id=?, local='mochila', slot=NULL WHERE id=?", (p["id"], l["inventory_id"]))
            d.execute("UPDATE market_listings SET status='vendido', comprador_id=? WHERE id=?", (p["id"], l["id"]))
        return jsonify(ok=True)

    @app.post("/api/mercado/cancelar")
    def cancelar():
        p, c = atual(), request.get_json(force=True)
        with tx(db()):
            r = db().execute("UPDATE market_listings SET status='cancelado' WHERE id=? AND seller_id=? AND status='ativo'", (c.get("listing_id"), p["id"]))
            if r.rowcount == 0:
                raise Erro("anúncio não encontrado", 404)
        return jsonify(ok=True)

    @app.get("/api/ranking")
    def ranking():
        atual()
        por = request.args.get("por", "nivel")
        col = {"nivel": "nivel DESC, xp DESC", "fama": "fama DESC"}.get(por)
        if not col:
            raise Erro("ranking inválido")
        rows = db().execute(f"SELECT nome, classe, nivel, fama FROM players ORDER BY {col} LIMIT 50").fetchall()
        return jsonify(ranking=[dict(r) for r in rows])

    # --- aldeia, trono e guerra (GDD 18, 25-29)
    @app.errorhandler(G.RegraErro)
    def regra_erro(e):
        return jsonify(erro=e.msg), e.code

    @app.get("/api/aldeia")
    def aldeia():
        p = atual()
        with tx(db()):
            est = G.estado_aldeia(db(), jogador(db(), p["id"]))
        return jsonify(est)

    @app.post("/api/trono/coroar")
    def coroar():
        p, c = atual(), request.get_json(force=True, silent=True) or {}
        with tx(db()):
            G.manutencao(db(), p["village_id"])
            ok, motivo = G.pode_coroar(db(), p)
            if not ok:
                raise Erro(motivo, 403)
            G.coroar(db(), p["village_id"], p["id"], str(c.get("discurso") or ""))
        return jsonify(G.estado_aldeia(db(), jogador(db(), p["id"])))

    @app.post("/api/trono/desafiar")
    def desafiar():
        p = atual()
        if em_luta(p["id"]):
            raise Erro("termine a luta atual", 409)
        with tx(db()):
            G.manutencao(db(), p["village_id"])
            kage, ctx = G.iniciar_desafio(db(), p)
            estado = iniciar_combate(db(), p, G.lutador_normalizado(kage, db()), "trono", random.choice(list(C.AMBIENTES)), ctx,
                                     jogador_l=G.lutador_normalizado(p, db()), decisor_rival=G.decisor_de(db(), kage["id"]))
        return jsonify(combate=estado)

    @app.post("/api/trono/desconfianca")
    def desconfianca():
        p = atual()
        with tx(db()):
            G.manutencao(db(), p["village_id"])
            r = G.voto_desconfianca(db(), p)
        return jsonify(r)

    @app.get("/api/playbook")
    def ver_playbook():
        p = atual()
        row = db().execute("SELECT regras_json FROM playbooks WHERE player_id=?", (p["id"],)).fetchone()
        return jsonify(regras=json.loads(row[0]) if row else [])

    @app.post("/api/playbook")
    def salvar_playbook():
        p, c = atual(), request.get_json(force=True)
        regras = G.validar_playbook(c.get("regras"))
        with tx(db()):
            db().execute("INSERT INTO playbooks(player_id,regras_json) VALUES (?,?) ON CONFLICT(player_id) DO UPDATE SET regras_json=excluded.regras_json",
                         (p["id"], json.dumps(regras, ensure_ascii=False)))
        return jsonify(regras=regras)

    @app.post("/api/guerra/declarar")
    def guerra_declarar():
        p = atual()
        with tx(db()):
            G.manutencao(db(), p["village_id"])
            r = G.declarar_guerra(db(), p)
        return jsonify(r)

    @app.post("/api/guerra/votar")
    def guerra_votar():
        p, c = atual(), request.get_json(force=True, silent=True) or {}
        with tx(db()):
            r = G.votar_guerra(db(), p, bool(c.get("apoiar", True)))
        return jsonify(r)

    @app.post("/api/guerra/lutar")
    def guerra_lutar():
        p = atual()
        if em_luta(p["id"]):
            raise Erro("termine a luta atual", 409)
        with tx(db()):
            rival, dec, ctx = G.iniciar_luta_guerra(db(), p)
            estado = iniciar_combate(db(), p, rival, "guerra", random.choice(list(C.AMBIENTES)), ctx,
                                     jogador_l=G.lutador_normalizado(p, db()), decisor_rival=dec)
        return jsonify(combate=estado)

    # --- jutsus, selos e builds (GDD 19)
    @app.get("/api/jutsus")
    def jutsus_estado():
        p = atual()
        with tx(db()):
            return jsonify(J.estado(db(), p))

    @app.post("/api/jutsus/aprender")
    def jutsus_aprender():
        p, c = atual(), request.get_json(force=True)
        with tx(db()):
            return jsonify(J.aprender(db(), p, c.get("chave")))

    @app.post("/api/jutsus/equipar")
    def jutsus_equipar():
        p, c = atual(), request.get_json(force=True)
        with tx(db()):
            return jsonify(J.equipar(db(), p, c.get("chave"), bool(c.get("equipar", True))))

    @app.post("/api/jutsus/respec")
    def jutsus_respec():
        p = atual()
        if em_luta(p["id"]):
            raise Erro("termine a luta atual", 409)
        with tx(db()):
            return jsonify(J.respec(db(), p, mover))

    @app.post("/api/selos/comprar")
    def selos_comprar():
        p, c = atual(), request.get_json(force=True)
        with tx(db()):
            return jsonify(J.comprar_selo(db(), p, c.get("chave"), mover))

    @app.post("/api/selos/encaixar")
    def selos_encaixar():
        p, c = atual(), request.get_json(force=True)
        with tx(db()):
            return jsonify(J.encaixar(db(), p, int(c.get("inventory_id", 0)), c.get("chave")))

    @app.post("/api/selos/remover")
    def selos_remover():
        p, c = atual(), request.get_json(force=True)
        with tx(db()):
            return jsonify(J.remover_selo(db(), p, int(c.get("inventory_id", 0)), c.get("chave")))

    @app.post("/api/selos/supremo")
    def selos_supremo():
        p, c = atual(), request.get_json(force=True)
        with tx(db()):
            return jsonify(J.escolher_supremo(db(), p, c.get("chave"), mover))

    @app.post("/api/build/importar")
    def build_importar():
        p, c = atual(), request.get_json(force=True)
        with tx(db()):
            return jsonify(J.importar(db(), p, str(c.get("codigo", ""))))

    @app.get("/api/hall")
    def hall():
        atual()
        rows = db().execute("""SELECT v.nome AS aldeia, h.numero, h.player_nome, h.discurso, h.dias_reinado
                               FROM hall_of_kages h JOIN villages v ON v.id=h.village_id ORDER BY h.id DESC LIMIT 50""").fetchall()
        return jsonify(hall=[dict(r) for r in rows])

    @app.get("/api/jornal")
    def jornal():
        atual()
        rows = db().execute("SELECT texto, criado_em FROM world_chronicle ORDER BY id DESC LIMIT 30").fetchall()
        return jsonify(jornal=[dict(r) for r in rows])

    return app


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
