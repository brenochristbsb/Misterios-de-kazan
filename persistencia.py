"""
persistencia.py — Proteção do banco de dados do RPG Ninja contra perda em atualizações.

POR QUE OS DADOS SOMEM NO DEPLOY
    Num serviço do Render sem disco persistente, o sistema de arquivos é apagado a cada deploy.
    Se o banco é o arquivo SQLite padrão (ninja_rpg.db), ele some junto. A solução de verdade é
    guardar o banco FORA do código: Postgres (DATABASE_URL) ou um Disco Persistente do Render.
    Este módulo cuida do resto, em camadas:

    1. Escolhe o banco certo sozinho: DATABASE_URL > SQLITE_PATH > /var/data (disco do Render) > ./ninja_rpg.db
    2. Faz backup completo (JSON compactado) antes de migrar, a cada poucas horas e ao encerrar o servidor.
    3. Se o banco vier VAZIO, restaura sozinho do backup mais recente (pasta de backups ou backup_inicial.json.gz).
    4. Opcional: manda uma cópia do backup para o seu Telegram (ADMIN_CHAT_ID), que sobrevive a qualquer deploy.
    5. Rotas protegidas por ADMIN_TOKEN para baixar e restaurar backups; também funciona pela linha de comando.

VARIÁVEIS DE AMBIENTE (todas opcionais)
    DATABASE_URL        banco externo (Postgres recomendado)
    SQLITE_PATH         caminho do arquivo SQLite (ex.: /var/data/ninja_rpg.db)
    BACKUP_DIR          pasta dos backups (padrão: ao lado do banco)
    BACKUP_HORAS        intervalo do backup automático (padrão 6)
    BACKUP_MANTER       quantos backups automáticos guardar (padrão 20)
    ADMIN_TOKEN         senha das rotas /api/admin (sem ela, as rotas ficam desligadas)
    ADMIN_CHAT_ID       seu chat_id no Telegram para receber o backup (usa o BOT_TOKEN do jogo)
    TELEGRAM_BACKUP_HORAS  intervalo de envio ao Telegram (padrão 24)

LINHA DE COMANDO
    python persistencia.py status
    python persistencia.py backup
    python persistencia.py restaurar arquivo.json.gz [--substituir]
"""
from __future__ import annotations

import base64
import enum
import gzip
import hmac
import json
import logging
import os
import sys
import threading
import urllib.request
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

log = logging.getLogger("ninja_persistencia")
FORMATO = "ninja-rpg-backup"
VERSAO = 1
BACKUP_INICIAL = Path(os.getenv("BACKUP_INICIAL", "backup_inicial.json.gz"))


# ─────────────────────────────────────────────────────────────
# Onde fica o banco e os backups
# ─────────────────────────────────────────────────────────────
def resolver_database_url() -> str:
    """URL do banco, escolhida na ordem: DATABASE_URL, SQLITE_PATH, disco persistente do Render, arquivo local."""
    url = os.getenv("DATABASE_URL", "").strip()
    if url:
        return "postgresql://" + url[len("postgres://"):] if url.startswith("postgres://") else url
    caminho = os.getenv("SQLITE_PATH", "").strip()
    if not caminho:
        disco = Path(os.getenv("DISCO_PERSISTENTE", "/var/data"))
        caminho = str(disco / "ninja_rpg.db") if disco.is_dir() and os.access(disco, os.W_OK) else "./ninja_rpg.db"
    arq = Path(caminho).expanduser().resolve()
    arq.parent.mkdir(parents=True, exist_ok=True)
    return "sqlite:///" + str(arq)


def _arquivo_sqlite(url: str) -> Optional[Path]:
    return Path(url.split("///", 1)[1]) if url.startswith("sqlite:///") else None


def pasta_backups(url: str) -> Path:
    env = os.getenv("BACKUP_DIR", "").strip()
    if env:
        pasta = Path(env)
    elif _arquivo_sqlite(url):
        pasta = _arquivo_sqlite(url).parent / "backups"
    else:
        pasta = Path("/var/data/backups") if Path("/var/data").is_dir() else Path("./backups")
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def banco_e_volatil(url: str) -> bool:
    """True se o banco provavelmente será apagado no próximo deploy (SQLite fora de disco persistente, no Render)."""
    arq = _arquivo_sqlite(url)
    if arq is None:
        return False
    no_render = bool(os.getenv("RENDER"))
    return no_render and not str(arq).startswith(os.getenv("DISCO_PERSISTENTE", "/var/data"))


# ─────────────────────────────────────────────────────────────
# Exportar / importar
# ─────────────────────────────────────────────────────────────
def _valor(v):
    if isinstance(v, enum.Enum):  # antes de tudo: enums com str misturado seriam gravados pelo valor
        return v.name
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, bytes):
        return {"__b64__": base64.b64encode(v).decode()}
    return v


def _de_json(col, v):
    from sqlalchemy import Date, DateTime, LargeBinary
    if v is None:
        return None
    if isinstance(col.type, DateTime) and isinstance(v, str):
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    if isinstance(col.type, Date) and not isinstance(col.type, DateTime) and isinstance(v, str):
        return date.fromisoformat(v)
    if isinstance(col.type, LargeBinary) and isinstance(v, dict) and "__b64__" in v:
        return base64.b64decode(v["__b64__"])
    return v


def exportar(engine, Base) -> dict:
    """Lê todas as tabelas do jogo e devolve um dicionário pronto para virar JSON."""
    from sqlalchemy import inspect
    insp = inspect(engine)
    tabelas: dict[str, list] = {}
    with engine.connect() as con:
        for t in Base.metadata.sorted_tables:
            if not insp.has_table(t.name):
                continue
            tabelas[t.name] = [{c.name: _valor(r._mapping[c]) for c in t.columns} for r in con.execute(t.select())]
    return {"formato": FORMATO, "versao": VERSAO, "gerado_em": datetime.now(timezone.utc).isoformat(), "tabelas": tabelas}


def _colunas_adiadas(Base) -> dict[str, list[str]]:
    """Colunas de chave estrangeira circular que aceitam NULL (jogador↔guilda, vila↔Kage): entram vazias e são preenchidas depois."""
    res: dict[str, list[str]] = {}
    for t in Base.metadata.tables.values():
        for fk in t.foreign_key_constraints:
            if fk.use_alter and all(c.nullable for c in fk.columns):
                res.setdefault(t.name, []).extend(c.name for c in fk.columns)
    return res


def _ordem(Base, adiadas) -> list:
    """Tabelas ordenadas para que cada uma entre depois das que ela referencia."""
    tabs = {t.name: t for t in Base.metadata.sorted_tables}
    deps = {}
    for nome, t in tabs.items():
        deps[nome] = {
            fk.referred_table.name for fk in t.foreign_key_constraints
            if fk.referred_table is not t and not (fk.use_alter and all(c.name in adiadas.get(nome, ()) for c in fk.columns))
        }
    ordem, feitas = [], set()
    while len(ordem) < len(tabs):
        prontas = [n for n in tabs if n not in feitas and deps[n] <= feitas]
        if not prontas:  # ciclo inesperado: segue na ordem do SQLAlchemy
            prontas = [n for n in tabs if n not in feitas][:1]
        for n in prontas:
            ordem.append(tabs[n])
            feitas.add(n)
    return ordem


def importar(engine, Base, dados: dict, modo: str = "mesclar") -> dict[str, int]:
    """
    Grava um backup no banco, tudo numa transação (se algo falhar, nada muda).
      mesclar:    só insere linhas cujo id ainda não existe (uso normal: restaurar num banco vazio).
      substituir: apaga tudo antes e coloca o conteúdo do backup.
    """
    from sqlalchemy import inspect, insert, select, text, update
    if not isinstance(dados, dict) or dados.get("formato") != FORMATO:
        raise ValueError("Arquivo inválido: não é um backup do RPG Ninja.")
    if dados.get("versao", 0) > VERSAO:
        raise ValueError("Backup de uma versão mais nova do jogo.")
    if modo not in ("mesclar", "substituir"):
        raise ValueError("modo deve ser 'mesclar' ou 'substituir'.")

    adiadas = _colunas_adiadas(Base)
    ordem = _ordem(Base, adiadas)
    insp = inspect(engine)
    contagem: dict[str, int] = {}
    with engine.begin() as con:
        if modo == "substituir":
            for t in reversed(ordem):
                if insp.has_table(t.name):
                    con.execute(t.delete())
        depois: dict[tuple, dict] = {}
        for t in ordem:
            linhas = dados["tabelas"].get(t.name) or []
            if not linhas or not insp.has_table(t.name):
                continue
            pk = list(t.primary_key.columns)
            existentes = {tuple(r) for r in con.execute(select(*pk))} if modo == "mesclar" else set()
            novos = []
            for lin in linhas:
                chave = tuple(lin.get(c.name) for c in pk)
                if chave in existentes:
                    continue
                reg = {c.name: _de_json(c, lin[c.name]) for c in t.columns if c.name in lin}
                for cn in adiadas.get(t.name, ()):
                    if reg.get(cn) is not None:
                        depois.setdefault((t.name, chave), {})[cn] = reg[cn]
                        reg[cn] = None
                novos.append(reg)
            for i in range(0, len(novos), 500):
                con.execute(insert(t), novos[i:i + 500])
            contagem[t.name] = len(novos)
        for (nome, chave), vals in depois.items():
            t = Base.metadata.tables[nome]
            con.execute(update(t).where(*[c == v for c, v in zip(t.primary_key.columns, chave)]).values(**vals))
        if engine.dialect.name == "postgresql":  # ids explícitos não avançam a sequência: realinha
            for t in ordem:
                pk = list(t.primary_key.columns)
                if len(pk) == 1 and pk[0].autoincrement is not False and str(pk[0].type) in ("INTEGER", "BIGINT") and insp.has_table(t.name):
                    con.execute(text(
                        f"SELECT setval(pg_get_serial_sequence('\"{t.name}\"', '{pk[0].name}'), "
                        f"COALESCE(MAX(\"{pk[0].name}\"), 1), MAX(\"{pk[0].name}\") IS NOT NULL) FROM \"{t.name}\""
                    ))
    return contagem


# ─────────────────────────────────────────────────────────────
# Arquivos de backup
# ─────────────────────────────────────────────────────────────
def contar_jogadores(engine, Base) -> int:
    from sqlalchemy import func, inspect, select
    t = Base.metadata.tables.get("players")
    if t is None or not inspect(engine).has_table("players"):
        return 0
    with engine.connect() as con:
        return con.execute(select(func.count()).select_from(t)).scalar() or 0


def _ler_arquivo(bruto: bytes) -> dict:
    if bruto[:2] == b"\x1f\x8b":
        bruto = gzip.decompress(bruto)
    return json.loads(bruto.decode("utf-8"))


def salvar_backup(engine, Base, rotulo: str = "auto") -> Path:
    """Grava um backup .json.gz na pasta de backups (escrita atômica) e apara os automáticos antigos."""
    pasta = pasta_backups(str(engine.url.render_as_string(hide_password=False)))
    nome = f"ninja_backup_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_{rotulo}.json.gz"
    destino, tmp = pasta / nome, pasta / (nome + ".tmp")
    tmp.write_bytes(gzip.compress(json.dumps(exportar(engine, Base), ensure_ascii=False).encode("utf-8")))
    tmp.replace(destino)
    manter = int(os.getenv("BACKUP_MANTER", "20"))
    antigos = sorted(p for p in pasta.glob("ninja_backup_*_auto.json.gz"))
    for p in antigos[:-manter] if manter > 0 else []:
        p.unlink(missing_ok=True)
    return destino


def ultimo_backup(url: str) -> Optional[Path]:
    arqs = sorted(pasta_backups(url).glob("ninja_backup_*.json.gz"))
    return arqs[-1] if arqs else (BACKUP_INICIAL if BACKUP_INICIAL.is_file() else None)


def _enviar_telegram(arq: Path, legenda: str) -> bool:
    """Manda o backup para o seu chat do Telegram: uma cópia fora do servidor, que sobrevive a qualquer deploy."""
    token, chat = os.getenv("BOT_TOKEN"), os.getenv("ADMIN_CHAT_ID")
    if not (token and chat):
        return False
    if arq.stat().st_size > 45 * 1024 * 1024:
        log.warning("Backup grande demais para o Telegram (%s bytes).", arq.stat().st_size)
        return False
    lim = uuid.uuid4().hex
    partes = []
    for k, v in (("chat_id", chat), ("caption", legenda)):
        partes.append(f'--{lim}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    partes.append(f'--{lim}\r\nContent-Disposition: form-data; name="document"; filename="{arq.name}"\r\n'
                  f'Content-Type: application/gzip\r\n\r\n'.encode() + arq.read_bytes() + b"\r\n")
    partes.append(f"--{lim}--\r\n".encode())
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendDocument", data=b"".join(partes),
                                 headers={"Content-Type": f"multipart/form-data; boundary={lim}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status == 200
    except Exception as e:  # nunca derruba o jogo por causa do backup
        log.warning("Falha ao enviar backup ao Telegram: %s", e)
        return False


# ─────────────────────────────────────────────────────────────
# Ganchos para o api.py
# ─────────────────────────────────────────────────────────────
_parar = threading.Event()
_ultimo_telegram = 0.0


def _backup_seguro(engine, Base, rotulo: str, telegram: bool = False) -> None:
    global _ultimo_telegram
    try:
        if contar_jogadores(engine, Base) == 0:
            return  # nunca sobrescreve a lista de backups com um banco vazio
        arq = salvar_backup(engine, Base, rotulo)
        log.info("Backup salvo: %s", arq)
        intervalo = float(os.getenv("TELEGRAM_BACKUP_HORAS", "24")) * 3600
        agora = datetime.now(timezone.utc).timestamp()
        if telegram or agora - _ultimo_telegram >= intervalo:
            if _enviar_telegram(arq, f"Backup do RPG Ninja ({rotulo})"):
                _ultimo_telegram = agora
    except Exception:
        log.exception("Falha ao criar backup")


def proteger_antes(engine, Base) -> None:
    """Chame ANTES do create_all/ensure_columns: avisa se o banco é volátil e guarda uma cópia antes de migrar."""
    url = str(engine.url.render_as_string(hide_password=False))
    if banco_e_volatil(url):
        log.warning("ATENÇÃO: o banco SQLite está em disco temporário do Render e será APAGADO no próximo deploy. "
                    "Use DATABASE_URL (Postgres) ou anexe um Disco Persistente em /var/data.")
    _backup_seguro(engine, Base, "deploy")


def proteger_depois(engine, Base) -> None:
    """Chame DEPOIS do create_all e ANTES do seed: restaura sozinho se o banco veio vazio e inicia o backup periódico."""
    url = str(engine.url.render_as_string(hide_password=False))
    if contar_jogadores(engine, Base) == 0:
        arq = ultimo_backup(url)
        if arq is not None:
            try:
                n = importar(engine, Base, _ler_arquivo(arq.read_bytes()), "mesclar")
                log.warning("Banco vazio: restaurado de %s (%s)", arq, n)
            except Exception:
                log.exception("Não consegui restaurar de %s", arq)

    horas = float(os.getenv("BACKUP_HORAS", "6"))

    def ciclo():
        while not _parar.wait(horas * 3600):
            _backup_seguro(engine, Base, "auto")

    threading.Thread(target=ciclo, name="backup-periodico", daemon=True).start()


def proteger_ao_encerrar(engine, Base) -> None:
    """Chame quando o servidor encerra (o Render manda desligar antes de cada deploy): última cópia, enviada ao Telegram."""
    _parar.set()
    _backup_seguro(engine, Base, "auto", telegram=True)


# ─────────────────────────────────────────────────────────────
# Rotas de administração (sub-aplicação separada, fora do login do Telegram)
# ─────────────────────────────────────────────────────────────
def criar_app_admin(engine, Base):
    """Sub-aplicação para montar em /api/admin. Exige ADMIN_TOKEN (cabeçalho X-Admin-Token ou ?token=)."""
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import JSONResponse, Response

    adm = FastAPI(title="Admin do RPG Ninja", docs_url=None, redoc_url=None, openapi_url=None)
    url = str(engine.url.render_as_string(hide_password=False))

    def checar(req: Request) -> None:
        esperado = os.getenv("ADMIN_TOKEN", "")
        if not esperado:
            raise HTTPException(503, "Rotas de admin desligadas: defina ADMIN_TOKEN.")
        enviado = req.headers.get("x-admin-token") or req.query_params.get("token", "")
        if not hmac.compare_digest(enviado.encode(), esperado.encode()):
            raise HTTPException(403, "Token inválido.")

    @adm.get("/status")
    def status(req: Request):
        checar(req)
        return {"banco": engine.dialect.name, "volatil": banco_e_volatil(url), "jogadores": contar_jogadores(engine, Base),
                "backups": [p.name for p in sorted(pasta_backups(url).glob("ninja_backup_*.json.gz"))[-10:]]}

    @adm.get("/backup")
    def baixar(req: Request):
        checar(req)
        corpo = gzip.compress(json.dumps(exportar(engine, Base), ensure_ascii=False).encode("utf-8"))
        nome = f"ninja_backup_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.json.gz"
        return Response(corpo, media_type="application/gzip", headers={"Content-Disposition": f'attachment; filename="{nome}"'})

    @adm.post("/backup-agora")
    def agora(req: Request):
        checar(req)
        return {"arquivo": salvar_backup(engine, Base, "manual").name}

    @adm.post("/restaurar")
    async def restaurar(req: Request):
        checar(req)
        modo = req.query_params.get("modo", "mesclar")
        if modo == "substituir" and req.query_params.get("confirmar") != "SIM":
            raise HTTPException(400, "Para substituir tudo, adicione &confirmar=SIM.")
        try:
            dados = _ler_arquivo(await req.body())
            if contar_jogadores(engine, Base):
                salvar_backup(engine, Base, "antes_restaurar")  # rede de segurança antes de mexer
            return JSONResponse({"restaurado": importar(engine, Base, dados, modo)})
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            log.exception("Falha ao restaurar")
            raise HTTPException(409, f"Não foi possível restaurar (nada foi alterado): {type(e).__name__}")

    return adm


# ─────────────────────────────────────────────────────────────
# Linha de comando
# ─────────────────────────────────────────────────────────────
def _main(argv: list[str]) -> int:
    from sqlalchemy import create_engine
    from models import Base
    url = resolver_database_url()
    engine = create_engine(url, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "status":
        print(f"Banco: {engine.dialect.name} ({'volátil!' if banco_e_volatil(url) else 'ok'}) | jogadores: {contar_jogadores(engine, Base)}")
        print(f"Backups em {pasta_backups(url)}: {len(list(pasta_backups(url).glob('ninja_backup_*.json.gz')))}")
    elif cmd == "backup":
        print("Salvo em", salvar_backup(engine, Base, "manual"))
    elif cmd == "restaurar" and len(argv) > 2:
        modo = "substituir" if "--substituir" in argv else "mesclar"
        if contar_jogadores(engine, Base):
            print("Backup de segurança:", salvar_backup(engine, Base, "antes_restaurar"))
        Base.metadata.create_all(bind=engine)
        print(importar(engine, Base, _ler_arquivo(Path(argv[2]).read_bytes()), modo))
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
