import os
import random
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from cidade import ENERGIA_MAX, MIN_POR_ENERGIA, MISSOES, ITENS

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_URL = os.environ.get("DATABASE_URL")      # definida => Postgres; senão SQLite (desenvolvimento)
USAR_PG = bool(DATABASE_URL)
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "kazan.db"))
TRAVAR = " FOR UPDATE" if USAR_PG else ""           # trava a linha do jogador contra ações simultâneas

# Colunas que as conquistas podem premiar (lista fixa por segurança)
COLUNAS_RECOMPENSA = {"moedas", "moedas_eter", "exp"}

CONQUISTAS = [
    ("primeira_vitoria", "Primeiro Passo", "Vença sua primeira batalha", "⚔️", "moedas_eter", 10),
    ("explorador", "Explorador Iniciante", "Explore 5 vezes", "🗺️", "moedas_eter", 20),
    ("convidado", "Anfitrião", "Convide um amigo para o jogo", "🤝", "moedas_eter", 50),
    ("nivel_5", "Caminho do Aprendiz", "Alcance o nível 5", "⭐", "moedas_eter", 30),
]

_pool = None


def _get_pool():
    global _pool
    if _pool is None:
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
        _pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=8, kwargs={"row_factory": dict_row}, open=True)
    return _pool


class _Con:
    """Mesma interface para SQLite e Postgres: escreva SQL com '?' e use sempre rows por nome."""
    def __init__(self, con):
        self._c = con

    def execute(self, sql, params=()):
        if USAR_PG:
            sql = sql.replace("%", "%%").replace("?", "%s")
        return self._c.execute(sql, params)


@contextmanager
def db():
    if USAR_PG:
        with _get_pool().connection() as con:   # commit/rollback automáticos
            yield _Con(con)
        return
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield _Con(con)
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db():
    with open(os.path.join(BASE_DIR, "esquema.sql"), encoding="utf-8") as f:
        esquema = f.read()
    if USAR_PG:
        esquema = esquema.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY") \
                         .replace("telegram_id INTEGER", "telegram_id BIGINT")   # IDs do Telegram passam de 2 bilhões
    with db() as con:
        for comando in esquema.split(";"):
            if comando.strip():
                con.execute(comando)
        if not USAR_PG:  # bancos SQLite antigos sem a coluna telegram_id
            colunas = [r["name"] for r in con.execute("PRAGMA table_info(players)")]
            if "telegram_id" not in colunas:
                con.execute("ALTER TABLE players ADD COLUMN telegram_id INTEGER")
            con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_players_telegram ON players(telegram_id)")
        for c in CONQUISTAS:
            con.execute("INSERT INTO conquistas(chave, nome, descricao, icone, recompensa_tipo, recompensa_valor) "
                        "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (chave) DO NOTHING", c)


MAX_NIVEL = 100


def exp_total(n):
    """EXP acumulada necessária para ALCANÇAR o nível n (curva: nível 10 ≈ 2,6 mil; 50 ≈ 55 mil; 100 ≈ 190 mil)."""
    return round(50 * (max(1, n) - 1) ** 1.8)


def nivel_para_exp(exp):
    n = 1
    while n < MAX_NIVEL and exp >= exp_total(n + 1):
        n += 1
    return n


def exp_para_proximo(nivel):
    n = min(max(1, nivel), MAX_NIVEL - 1)
    return exp_total(n + 1) - exp_total(n)


def exp_exploracao(nivel):
    """Uma exploração rende ~12% do nível atual: ~8 explorações por nível, em qualquer nível."""
    return max(1, round(random.uniform(0.8, 1.2) * exp_para_proximo(nivel) * 0.12))


def _agora():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def regenerar_energia(con, jogador_id):
    j = con.execute("SELECT energia, ultimo_energia FROM players WHERE id = ?", (jogador_id,)).fetchone()
    agora, energia = _agora(), j["energia"] if j["energia"] is not None else ENERGIA_MAX
    raw = j["ultimo_energia"]
    try:
        ultimo = raw.replace(tzinfo=None) if isinstance(raw, datetime) else datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    except Exception:
        ultimo = agora
    if energia >= ENERGIA_MAX:
        nova, novo_ultimo = ENERGIA_MAX, agora
    else:
        ganhos = int((agora - ultimo).total_seconds() // (MIN_POR_ENERGIA * 60))
        nova = min(ENERGIA_MAX, energia + max(0, ganhos))
        novo_ultimo = agora if nova >= ENERGIA_MAX else ultimo + timedelta(minutes=max(0, ganhos) * MIN_POR_ENERGIA)
    con.execute("UPDATE players SET energia = ?, ultimo_energia = ? WHERE id = ?",
                (nova, novo_ultimo.strftime("%Y-%m-%d %H:%M:%S"), jogador_id))


def obter_ou_criar_jogador(con, telegram_id, nome):
    row = con.execute("SELECT * FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone()
    if not row:
        while True:
            codigo = secrets.token_hex(4).upper()
            if not con.execute("SELECT 1 FROM players WHERE codigo_convite = ?", (codigo,)).fetchone():
                break
        con.execute(
            "INSERT INTO players(telegram_id, nome, codigo_convite) VALUES (?, ?, ?) ON CONFLICT (telegram_id) DO NOTHING",
            (telegram_id, nome or "Viajante", codigo),
        )
        row = con.execute("SELECT * FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone()
    regenerar_energia(con, row["id"])
    return con.execute("SELECT * FROM players WHERE id = ?" + TRAVAR, (row["id"],)).fetchone()


def jogador_dict(row):
    return {
        "id": row["id"],
        "nome": row["nome"],
        "aldeia_id": row["aldeia_id"],
        "tradicao_id": row["tradicao_id"],
        "nivel": row["nivel"],
        "exp": row["exp"],
        "exp_base": exp_total(row["nivel"]),
        "exp_prox": exp_total(min(MAX_NIVEL, row["nivel"] + 1)),
        "moedas": row["moedas"],
        "moedas_eter": row["moedas_eter"],
        "energia": row["energia"],
        "codigo_convite": row["codigo_convite"],
    }


def progresso_do_jogador(con, jogador_id):
    rows = con.execute(
        "SELECT saga_id, ultimo_capitulo, concluido FROM progresso_historia WHERE player_id = ?",
        (jogador_id,),
    ).fetchall()
    return {
        r["saga_id"]: {"ultimo_capitulo": r["ultimo_capitulo"], "concluido": bool(r["concluido"])}
        for r in rows
    }


def salvar_progresso(con, jogador_id, saga_id, ultimo_capitulo, concluido):
    # Upsert que preserva a linha (INSERT OR REPLACE apagaria o campo "concluido")
    con.execute(
        """
        INSERT INTO progresso_historia(player_id, saga_id, ultimo_capitulo, concluido, desbloqueado_em)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(player_id, saga_id) DO UPDATE SET
            ultimo_capitulo = excluded.ultimo_capitulo,
            concluido = excluded.concluido,
            desbloqueado_em = excluded.desbloqueado_em
        """,
        (jogador_id, saga_id, ultimo_capitulo, 1 if concluido else 0),
    )


def conceder_conquista(con, jogador_id, chave):
    """Concede a conquista uma única vez e paga a recompensa. Retorna dict ou None."""
    c = con.execute("SELECT * FROM conquistas WHERE chave = ?", (chave,)).fetchone()
    if not c:
        return None
    cur = con.execute(
        "INSERT INTO conquistas_jogador(player_id, conquista_id) VALUES (?, ?) ON CONFLICT DO NOTHING",
        (jogador_id, c["id"]),
    )
    if cur.rowcount == 0:
        return None
    tipo, valor = c["recompensa_tipo"], c["recompensa_valor"] or 0
    if tipo in COLUNAS_RECOMPENSA and valor:
        con.execute(f"UPDATE players SET {tipo} = {tipo} + ? WHERE id = ?", (valor, jogador_id))
    return {"nome": c["nome"], "icone": c["icone"], "recompensa_tipo": tipo, "recompensa_valor": valor}


# ---------------- Contadores, missões e recompensas ----------------
def dia_atual():
    return (_agora() - timedelta(hours=3)).strftime("%Y-%m-%d")  # dia no horário de Brasília


def contar(con, jogador_id, chave, n=1):
    """Soma n ao contador total e ao contador do dia."""
    for k in (chave, f"{dia_atual()}:{chave}"):
        con.execute(
            "INSERT INTO contadores(player_id, chave, valor) VALUES (?, ?, ?) "
            "ON CONFLICT(player_id, chave) DO UPDATE SET valor = valor + excluded.valor",
            (jogador_id, k, n),
        )


def valor(con, jogador_id, chave):
    r = con.execute("SELECT valor FROM contadores WHERE player_id = ? AND chave = ?", (jogador_id, chave)).fetchone()
    return r["valor"] if r else 0


def conceder(con, jogador_id, exp=0, moedas=0, eter=0):
    """Paga recompensa, atualiza nível e conquistas. Retorna (subiu_nivel, conquistas)."""
    j = con.execute("SELECT exp, nivel FROM players WHERE id = ?", (jogador_id,)).fetchone()
    nivel = max(j["nivel"], nivel_para_exp(j["exp"] + exp))
    con.execute("UPDATE players SET exp = exp + ?, nivel = ?, moedas = moedas + ?, moedas_eter = moedas_eter + ? WHERE id = ?",
                (exp, nivel, moedas, eter, jogador_id))
    conquistas = []
    if nivel >= 5:
        c = conceder_conquista(con, jogador_id, "nivel_5")
        if c:
            conquistas.append(c)
    return nivel > j["nivel"], conquistas


def missoes_do_dia(con, jogador_id, nivel=1):
    d = dia_atual()
    return [
        {**m, "exp": round(exp_para_proximo(nivel) * m["exp"] / 100), "moedas": round(m["moedas"] * (1 + nivel / 5)),
         "progresso": min(valor(con, jogador_id, f"{d}:{m['contador']}"), m["meta"]),
         "coletada": valor(con, jogador_id, f"{d}:coletada:{m['id']}") > 0}
        for m in MISSOES
    ]


# ---------------- Inventário ----------------
def add_item(con, jogador_id, item_id, n=1):
    con.execute(
        "INSERT INTO inventario(player_id, item_id, qtd) VALUES (?, ?, ?) "
        "ON CONFLICT(player_id, item_id) DO UPDATE SET qtd = qtd + excluded.qtd",
        (jogador_id, item_id, n),
    )


def linha_item(con, jogador_id, item_id):
    return con.execute("SELECT * FROM inventario WHERE player_id = ? AND item_id = ? AND qtd > 0",
                       (jogador_id, item_id)).fetchone()


def bonus_equipado(con, jogador_id):
    """Multiplicadores (exp, moedas) dos equipamentos equipados."""
    mult = {"exp": 1.0, "moedas": 1.0, "eter": 0.0}
    for r in con.execute("SELECT item_id, nivel FROM inventario WHERE player_id = ? AND equipado = 1 AND qtd > 0", (jogador_id,)):
        for k, v in ITENS.get(r["item_id"], {}).get("bonus", {}).items():
            mult[k] += v * (1 + r["nivel"]) / 100
    return mult["exp"], mult["moedas"], mult["eter"]


# ---------------- Combate (Arena) ----------------
def stats_combate(con, jogador_id, nivel):
    atk, dfs, vida = 10 + nivel * 3, 5 + nivel * 2, 100 + nivel * 20
    for r in con.execute("SELECT item_id, nivel FROM inventario WHERE player_id = ? AND equipado = 1 AND qtd > 0", (jogador_id,)):
        s, f = ITENS.get(r["item_id"], {}).get("stats", {}), 1 + 0.25 * r["nivel"]   # cada melhoria +25%
        atk += s.get("ataque", 0) * f
        dfs += s.get("defesa", 0) * f
        vida += s.get("vida", 0) * f
    return {"ataque": round(atk), "defesa": round(dfs), "vida": round(vida)}


def _golpe(atk, dfs, rng):
    dano = atk * 100 / (100 + dfs) * rng.uniform(0.85, 1.15)
    if rng.random() < 0.10:
        dano *= 1.5   # crítico
    return max(1, round(dano))


def simular_combate(a, b, rng=random):
    """Turnos alternados (a ataca primeiro). Retorna (a_venceu, rodadas)."""
    ha, hb, rodadas = a["vida"], b["vida"], []
    for _ in range(40):
        da = _golpe(a["ataque"], b["defesa"], rng)
        hb = max(0, hb - da)
        db_ = 0
        if hb > 0:
            db_ = _golpe(b["ataque"], a["defesa"], rng)
            ha = max(0, ha - db_)
        rodadas.append({"a": da, "b": db_, "ha": ha, "hb": hb})
        if ha == 0 or hb == 0:
            break
    venceu = True if hb == 0 else False if ha == 0 else ha / a["vida"] >= hb / b["vida"]
    return venceu, rodadas
