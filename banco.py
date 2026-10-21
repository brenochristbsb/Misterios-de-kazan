import os
import secrets
import sqlite3
from contextlib import contextmanager

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


def obter_ou_criar_jogador(con, telegram_id, nome):
    row = con.execute("SELECT * FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone()
    if row:
        return row
    while True:
        codigo = secrets.token_hex(4).upper()
        if not con.execute("SELECT 1 FROM players WHERE codigo_convite = ?", (codigo,)).fetchone():
            break
    con.execute(
        "INSERT INTO players(telegram_id, nome, codigo_convite) VALUES (?, ?, ?)",
        (telegram_id, nome or "Viajante", codigo),
    )
    return con.execute("SELECT * FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone()


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
