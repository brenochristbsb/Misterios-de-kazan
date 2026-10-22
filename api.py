import hashlib
import hmac
import json
import os
import random
import time
from dataclasses import asdict
from functools import wraps
from urllib.parse import parse_qsl

import telebot
from flask import Flask, request, jsonify, send_from_directory, g

import banco
import cidade
import dados
import sistema_historias as hist
from bot import bot, TOKEN

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------
def configurar_webhook():
    """Registra o webhook no Telegram usando a URL pública do Render."""
    base = os.environ.get("WEBHOOK_URL") or os.environ.get("RENDER_EXTERNAL_URL")
    if not base:
        print("WEBHOOK_URL/RENDER_EXTERNAL_URL não definida; webhook não configurado.")
        return
    try:
        bot.remove_webhook()
        bot.set_webhook(url=base.rstrip("/") + "/webhook")
        print("Webhook configurado em", base)
    except Exception as e:
        print("Falha ao configurar webhook:", e)


# ---------------------------------------------------------------------------
# Autenticação do Mini App (valida o initData assinado pelo Telegram)
# ---------------------------------------------------------------------------
def validar_init_data(init_data, token, max_idade=86400):
    try:
        pares = dict(parse_qsl(init_data, keep_blank_values=True))
        recebido = pares.pop("hash", None)
        if not recebido:
            return None
        texto = "\n".join(f"{k}={v}" for k, v in sorted(pares.items()))
        segredo = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        calculado = hmac.new(segredo, texto.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calculado, recebido):
            return None
        if time.time() - int(pares.get("auth_date", "0")) > max_idade:
            return None
        return json.loads(pares["user"])
    except Exception:
        return None


def erro(msg, codigo=400):
    return jsonify({"erro": msg}), codigo


def exige_login(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = validar_init_data(request.headers.get("X-Telegram-Init-Data", ""), TOKEN)
        if not user or "id" not in user:
            return erro("Abra o jogo pelo Telegram.", 401)
        g.tg_user = user
        return f(*args, **kwargs)
    return wrapper


def buscar_saga(saga_id):
    for s in hist.TODAS_AS_SAGAS:
        if s.id == saga_id:
            return s
    return None


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
def create_app():
    app = Flask(__name__, static_folder=STATIC_DIR)
    banco.init_db()

    # --- Mini App ---
    @app.route("/")
    @app.route("/jogo")
    def jogo():
        # Procura em static/ e também na raiz, caso o arquivo tenha ido para lá
        for pasta in (STATIC_DIR, BASE_DIR):
            if os.path.isfile(os.path.join(pasta, "jogo.html")):
                return send_from_directory(pasta, "jogo.html")
        return ("Arquivo jogo.html não encontrado no servidor. "
                "Envie static/jogo.html para o GitHub e faça um novo deploy."), 404

    @app.route("/saude")
    def saude():
        return "🤖 Mistérios de Kazan — Bot está no ar!"

    # --- Dados públicos ---
    @app.route("/api/dados")
    def api_dados():
        return jsonify({"aldeias": dados.ALDEIAS, "tradicoes": dados.TRADICOES})

    @app.route("/api/sagas")
    def api_sagas():
        saida = []
        for s in hist.TODAS_AS_SAGAS:
            saida.append({
                "id": s.id,
                "titulo": s.titulo,
                "descricao": s.descricao,
                "capitulos": [c.id for c in s.capitulos],
                "primeiro_capitulo": s.capitulos[0].id if s.capitulos else None,
            })
        return jsonify(saida)

    @app.route("/api/saga/<saga_id>/capitulo/<cap_id>")
    def api_capitulo(saga_id, cap_id):
        cap = hist.buscar_capitulo(saga_id, cap_id)
        if not cap:
            return erro("Capítulo não encontrado.", 404)
        return jsonify(asdict(cap))

    # --- Jogador (exige login pelo Telegram) ---
    @app.route("/api/jogador")
    @exige_login
    def api_jogador():
        nome = g.tg_user.get("first_name") or g.tg_user.get("username") or "Viajante"
        with banco.db() as con:
            j = banco.obter_ou_criar_jogador(con, g.tg_user["id"], nome)
            return jsonify({
                "jogador": banco.jogador_dict(j),
                "progresso": banco.progresso_do_jogador(con, j["id"]),
            })

    @app.route("/api/perfil", methods=["POST"])
    @exige_login
    def api_perfil():
        corpo = request.get_json(silent=True) or {}
        aldeia = corpo.get("aldeia_id")
        tradicao = corpo.get("tradicao_id")
        if aldeia not in {a["id"] for a in dados.ALDEIAS}:
            return erro("Aldeia inválida.")
        if tradicao not in {t["id"] for t in dados.TRADICOES}:
            return erro("Tradição inválida.")
        with banco.db() as con:
            j = banco.obter_ou_criar_jogador(con, g.tg_user["id"], g.tg_user.get("first_name"))
            if j["aldeia_id"] or j["tradicao_id"]:
                return erro("Seu perfil já foi definido.", 403)
            con.execute(
                "UPDATE players SET aldeia_id = ?, tradicao_id = ? WHERE id = ?",
                (aldeia, tradicao, j["id"]),
            )
            j = con.execute("SELECT * FROM players WHERE id = ?", (j["id"],)).fetchone()
            return jsonify({"jogador": banco.jogador_dict(j)})

    @app.route("/api/saga/<saga_id>/capitulo/<cap_id>/concluir", methods=["POST"])
    @exige_login
    def api_concluir(saga_id, cap_id):
        saga = buscar_saga(saga_id)
        cap = hist.buscar_capitulo(saga_id, cap_id)
        if not saga or not cap:
            return erro("Capítulo não encontrado.", 404)
        ids = [c.id for c in saga.capitulos]
        idx = ids.index(cap_id)

        with banco.db() as con:
            j = banco.obter_ou_criar_jogador(con, g.tg_user["id"], g.tg_user.get("first_name"))
            if not (j["aldeia_id"] and j["tradicao_id"]):
                return erro("Escolha sua aldeia e tradição primeiro.", 403)

            prog = banco.progresso_do_jogador(con, j["id"]).get(saga_id)
            if prog is None:
                atual_idx = 0
            elif prog["concluido"]:
                atual_idx = len(ids)
            else:
                atual_idx = ids.index(prog["ultimo_capitulo"]) if prog["ultimo_capitulo"] in ids else 0

            if idx > atual_idx:
                return erro("Este capítulo ainda está bloqueado.", 403)
            if not hist.verificar_requisito(cap, {"nivel": j["nivel"]}):
                return erro("Nível insuficiente para este capítulo.", 403)

            recompensa, subiu, conquistas = None, False, []
            if idx == atual_idx:  # primeira vez: paga recompensa e avança
                rec = cap.recompensas or {}
                novo_exp = j["exp"] + rec.get("exp", 0)
                novas_moedas = j["moedas"] + rec.get("moedas", 0)
                nivel_antes = j["nivel"]
                nivel_novo = max(nivel_antes, banco.nivel_para_exp(novo_exp))
                con.execute(
                    "UPDATE players SET exp = ?, moedas = ?, nivel = ? WHERE id = ?",
                    (novo_exp, novas_moedas, nivel_novo, j["id"]),
                )
                if cap.proximo:
                    banco.salvar_progresso(con, j["id"], saga_id, cap.proximo, False)
                else:
                    banco.salvar_progresso(con, j["id"], saga_id, cap.id, True)
                recompensa = {"exp": rec.get("exp", 0), "moedas": rec.get("moedas", 0)}
                banco.contar(con, j["id"], "capitulo")
                subiu = nivel_novo > nivel_antes
                if nivel_novo >= 5:
                    c = banco.conceder_conquista(con, j["id"], "nivel_5")
                    if c:
                        conquistas.append(c)

            j = con.execute("SELECT * FROM players WHERE id = ?", (j["id"],)).fetchone()
            return jsonify({
                "jogador": banco.jogador_dict(j),
                "progresso": banco.progresso_do_jogador(con, j["id"]),
                "recompensa": recompensa,
                "subiu_nivel": subiu,
                "conquistas": conquistas,
            })

    # --- Cidade ---
    @app.route("/api/cidade")
    def api_cidade():
        return jsonify({
            "locais": cidade.LOCAIS, "refeicoes": cidade.REFEICOES, "rumores": cidade.RUMORES,
            "energia_max": cidade.ENERGIA_MAX, "min_por_energia": cidade.MIN_POR_ENERGIA,
            "areas": cidade.AREAS, "taxa_eter": cidade.TAXA_ETER, "custo_explorar": cidade.CUSTO_EXPLORAR,
        })

    @app.route("/api/taverna/refeicao", methods=["POST"])
    @exige_login
    def api_refeicao():
        corpo = request.get_json(silent=True) or {}
        ref = next((r for r in cidade.REFEICOES if r["id"] == corpo.get("id")), None)
        if not ref:
            return erro("Refeição inválida.")
        with banco.db() as con:
            j = banco.obter_ou_criar_jogador(con, g.tg_user["id"], g.tg_user.get("first_name"))
            if j["energia"] >= cidade.ENERGIA_MAX:
                return erro("Sua energia já está cheia.")
            if j["moedas"] < ref["custo"]:
                return erro("Moedas insuficientes.")
            nova = min(cidade.ENERGIA_MAX, j["energia"] + ref["energia"])
            con.execute("UPDATE players SET energia = ?, moedas = moedas - ? WHERE id = ?",
                        (nova, ref["custo"], j["id"]))
            banco.contar(con, j["id"], "comer")
            j2 = con.execute("SELECT * FROM players WHERE id = ?", (j["id"],)).fetchone()
            return jsonify({"jogador": banco.jogador_dict(j2), "energia_ganha": nova - j["energia"]})

    # --- Locais da cidade ---
    def _jogador(con):
        return banco.obter_ou_criar_jogador(con, g.tg_user["id"], g.tg_user.get("first_name"))

    @app.route("/api/avisos")
    @exige_login
    def api_avisos():
        with banco.db() as con:
            j = _jogador(con)
            av = []
            if j["energia"] >= cidade.CUSTO_EXPLORAR:
                av.append("portal")
            if any(m["progresso"] >= m["meta"] and not m["coletada"] for m in banco.missoes_do_dia(con, j["id"])):
                av.append("missoes")
            return jsonify(av)

    @app.route("/api/missoes")
    @exige_login
    def api_missoes():
        with banco.db() as con:
            return jsonify(banco.missoes_do_dia(con, _jogador(con)["id"]))

    @app.route("/api/missoes/<mid>/coletar", methods=["POST"])
    @exige_login
    def api_coletar(mid):
        with banco.db() as con:
            j = _jogador(con)
            m = next((m for m in banco.missoes_do_dia(con, j["id"]) if m["id"] == mid), None)
            if not m:
                return erro("Missão não encontrada.", 404)
            if m["coletada"] or m["progresso"] < m["meta"]:
                return erro("Esta missão não está disponível para coleta.", 403)
            banco.contar(con, j["id"], f"coletada:{mid}")
            subiu, conq = banco.conceder(con, j["id"], m["exp"], m["moedas"])
            j2 = con.execute("SELECT * FROM players WHERE id = ?", (j["id"],)).fetchone()
            return jsonify({"jogador": banco.jogador_dict(j2), "subiu_nivel": subiu, "conquistas": conq})

    @app.route("/api/conquistas")
    @exige_login
    def api_conquistas():
        with banco.db() as con:
            j = _jogador(con)
            rows = con.execute(
                "SELECT c.*, (SELECT 1 FROM conquistas_jogador cj WHERE cj.conquista_id = c.id AND cj.player_id = ?) AS ok "
                "FROM conquistas c ORDER BY c.id", (j["id"],)).fetchall()
            return jsonify([{"nome": r["nome"], "descricao": r["descricao"], "icone": r["icone"],
                             "recompensa_tipo": r["recompensa_tipo"], "recompensa_valor": r["recompensa_valor"],
                             "conquistada": bool(r["ok"])} for r in rows])

    @app.route("/api/banco/converter", methods=["POST"])
    @exige_login
    def api_converter():
        qtd = (request.get_json(silent=True) or {}).get("qtd")
        if qtd not in (1, 5):
            return erro("Quantidade inválida.")
        with banco.db() as con:
            j = _jogador(con)
            custo = cidade.TAXA_ETER * qtd
            if j["moedas"] < custo:
                return erro("Moedas insuficientes.")
            con.execute("UPDATE players SET moedas = moedas - ?, moedas_eter = moedas_eter + ? WHERE id = ?", (custo, qtd, j["id"]))
            j2 = con.execute("SELECT * FROM players WHERE id = ?", (j["id"],)).fetchone()
            return jsonify({"jogador": banco.jogador_dict(j2)})

    @app.route("/api/portal/explorar", methods=["POST"])
    @exige_login
    def api_explorar():
        with banco.db() as con:
            j = _jogador(con)
            if j["energia"] < cidade.CUSTO_EXPLORAR:
                return erro("Energia insuficiente.")
            if j["energia"] >= cidade.ENERGIA_MAX:  # o relógio de recarga começa agora
                con.execute("UPDATE players SET ultimo_energia = ? WHERE id = ?",
                            (banco._agora().strftime("%Y-%m-%d %H:%M:%S"), j["id"]))
            con.execute("UPDATE players SET energia = energia - ? WHERE id = ?", (cidade.CUSTO_EXPLORAR, j["id"]))
            exp, moedas, eter = random.randint(5, 15), random.randint(5, 20), 1 if random.random() < 0.1 else 0
            banco.contar(con, j["id"], "explorar")
            subiu, conq = banco.conceder(con, j["id"], exp, moedas, eter)
            if banco.valor(con, j["id"], "explorar") >= 5:
                c = banco.conceder_conquista(con, j["id"], "explorador")
                if c:
                    conq.append(c)
            j2 = con.execute("SELECT * FROM players WHERE id = ?", (j["id"],)).fetchone()
            return jsonify({"jogador": banco.jogador_dict(j2), "texto": random.choice(cidade.EXPLORACOES),
                            "exp": exp, "moedas": moedas, "eter": eter, "subiu_nivel": subiu, "conquistas": conq})

    @app.route("/api/ranking")
    @exige_login
    def api_ranking():
        with banco.db() as con:
            j = _jogador(con)
            top = con.execute("SELECT nome, nivel, exp, aldeia_id FROM players WHERE aldeia_id IS NOT NULL "
                              "ORDER BY exp DESC, id LIMIT 10").fetchall()
            minha = con.execute("SELECT COUNT(*) + 1 AS p FROM players WHERE exp > ?", (j["exp"],)).fetchone()["p"]
            return jsonify({"top": [dict(r) for r in top], "minha": minha})

    # --- Webhook do Telegram ---
    @app.route("/webhook", methods=["POST"])
    def webhook():
        if request.headers.get("content-type") == "application/json":
            update = telebot.types.Update.de_json(request.get_data().decode("utf-8"))
            bot.process_new_updates([update])
            return "!", 200
        return "Erro", 403

    configurar_webhook()
    return app
