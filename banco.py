import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from cidade import ENERGIA_MAX, MIN_POR_ENERGIA, MISSOES

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "kazan.db"))

# Colunas que as conquistas podem premiar (lista fixa por segurança)
COLUNAS_RECOMPENSA = {"moedas", "moedas_eter", "exp"}


@contextmanager
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db():
    with db() as con:
        with open(os.path.join(BASE_DIR, "esquema.sql"), encoding="utf-8") as f:
            con.executescript(f.read())
        # Bancos antigos criados sem a coluna telegram_id
        colunas = [r["name"] for r in con.execute("PRAGMA table_info(players)")]
        if "telegram_id" not in colunas:
            con.execute("ALTER TABLE players ADD COLUMN telegram_id INTEGER")
        con.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_players_telegram ON players(telegram_id)"
        )


def nivel_para_exp(exp):
    return 1 + exp // 100


def _agora():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def regenerar_energia(con, jogador_id):
    j = con.execute("SELECT energia, ultimo_energia FROM players WHERE id = ?", (jogador_id,)).fetchone()
    agora, energia = _agora(), j["energia"] if j["energia"] is not None else ENERGIA_MAX
    try:
        ultimo = datetime.strptime(j["ultimo_energia"], "%Y-%m-%d %H:%M:%S")
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
            "INSERT INTO players(telegram_id, nome, codigo_convite) VALUES (?, ?, ?)",
            (telegram_id, nome or "Viajante", codigo),
        )
        row = con.execute("SELECT * FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone()
    regenerar_energia(con, row["id"])
    return con.execute("SELECT * FROM players WHERE id = ?", (row["id"],)).fetchone()


def jogador_dict(row):
    return {
        "id": row["id"],
        "nome": row["nome"],
        "aldeia_id": row["aldeia_id"],
        "tradicao_id": row["tradicao_id"],
        "nivel": row["nivel"],
        "exp": row["exp"],
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
        "INSERT OR IGNORE INTO conquistas_jogador(player_id, conquista_id) VALUES (?, ?)",
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


def missoes_do_dia(con, jogador_id):
    d = dia_atual()
    return [
        {**m, "progresso": min(valor(con, jogador_id, f"{d}:{m['contador']}"), m["meta"]),
         "coletada": valor(con, jogador_id, f"{d}:coletada:{m['id']}") > 0}
        for m in MISSOES
    ]
