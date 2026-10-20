"""
api.py — Rotas HTTP (FastAPI) consumidas pelo Telegram Mini App do RPG Ninja.

Dependências:  pip install fastapi uvicorn sqlalchemy pydantic
Executar:      uvicorn api:app --reload
Variáveis:     DATABASE_URL  (padrão: sqlite:///./ninja_rpg.db)
               CORS_ORIGINS  (lista separada por vírgula; padrão: "*")

Endpoints:
    GET  /api/setup/options        -> vilas e clãs disponíveis (tela de cadastro)
    POST /api/register             -> cria o jogador
    GET  /api/player/{telegram_id} -> perfil completo
    POST /api/train                -> treino de atributos (gasta chakra + ryos, dá XP)
    POST /api/mission/start        -> inicia missão (valida rank)
    POST /api/mission/complete     -> coleta recompensa de missão finalizada
    POST /api/guild/war/attack     -> ataque a outra guilda

ATENÇÃO (segurança): aqui o telegram_id vem no corpo/URL para simplificar.
Em produção, valide o `initData` assinado do Telegram (HMAC com o token do bot)
e extraia o telegram_id dele, senão qualquer pessoa pode agir por outro jogador.
"""

import hashlib
import hmac
import json
import logging
import os
import random
import time
from contextvars import ContextVar
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Iterator, Literal, Optional
from urllib.parse import parse_qsl

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from clas_dados import CLANS
from vilas_dados import VILAS
from jutsus_dados import ELEMENTOS, QUIZ, elemento_do_teste
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, selectinload, sessionmaker

from models import (
    AttributeType,
    Base,
    Clan,
    Guild,
    Mission,
    MissionRank,
    MissionStatus,
    NinjaRank,
    Player,
    PlayerMission,
    PlayerSummon,
    Summon,
    Village,
)

logger = logging.getLogger("ninja_api")

# ─────────────────────────────────────────────────────────────
# Banco de dados / sessão
# ─────────────────────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./ninja_rpg.db")
# Alguns provedores entregam "postgres://", que o SQLAlchemy 2 não aceita
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """Dependência: uma sessão por requisição, sempre fechada no final."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────
# Regras de jogo (constantes fáceis de balancear)
# ─────────────────────────────────────────────────────────────
ATTRS = ("taijutsu", "ninjutsu", "genjutsu", "agilidade", "forca", "inteligencia", "chakra_attr", "defesa", "vigor", "destreza")

# Promoção automática de rank por nível (Kage é concedido à parte)
RANK_BY_LEVEL = [(1, NinjaRank.ESTUDANTE), (5, NinjaRank.GENIN), (12, NinjaRank.CHUNIN),
                 (20, NinjaRank.JONIN), (30, NinjaRank.ANBU)]

# Todo jogador nasce neste "clã" vazio e troca ao passar no teste de admissão
NO_CLAN_NAME = "Sem clã"

# Ordem de dificuldade das missões e rank máximo de missão que cada patente pode aceitar
MISSION_ORDER = [MissionRank.D, MissionRank.C, MissionRank.B, MissionRank.A, MissionRank.S]
MAX_MISSION_RANK = {
    NinjaRank.ESTUDANTE: MissionRank.D,
    NinjaRank.GENIN: MissionRank.C,
    NinjaRank.CHUNIN: MissionRank.B,
    NinjaRank.JONIN: MissionRank.A,
    NinjaRank.ANBU: MissionRank.S,
    NinjaRank.SANNIN: MissionRank.S,
    NinjaRank.KAGE: MissionRank.S,
}

# Treino (custo e ganho por repetição)
TRAIN_CHAKRA_COST = 0  # chakra só é gasto em luta
TRAIN_RYOS_COST = 25
TRAIN_ATTR_GAIN = 1
TRAIN_EXP_GAIN = 3

# Guerra de guildas
WAR_CHAKRA_COST = 20
WAR_DEFENDERS_COUNT = 5      # só os N melhores defensores contam
WAR_LOSS_DAMAGE = 15         # vida perdida pelo atacante ao perder
WAR_ROLL_RANGE = (0.85, 1.15)  # fator de sorte aplicado a ataque e defesa


def exp_for_level(level: int) -> int:
    """EXP total necessária para ALCANÇAR `level` (nível 1 = 0, nível 2 = 250, nível 3 = 1.000...)."""
    return 250 * (level - 1) ** 2  # nível 2 = 250 XP, nível 5 = 4.000, nível 10 = 20.250


def apply_exp(player: Player, amount: int) -> int:
    """Soma EXP e sobe de nível quantas vezes for preciso. Retorna níveis ganhos."""
    player.exp += amount
    gained = 0
    while player.exp >= exp_for_level(player.level + 1):
        player.level += 1
        player.vida_max += 10
        player.chakra_max += 5
        # Ao subir de nível, o ganho de máximo também entra no valor atual
        player.vida_atual += 10
        player.chakra_atual += 5
        gained += 1
        if player.level >= 10 and random.random() < 0.06:  # pequena chance de despertar mais um elemento
            livres = [e for e in ELEMENTOS if e != player.elemento and e not in (player.elementos_extra or [])]
            if livres:
                player.elementos_extra = [*(player.elementos_extra or []), random.choice(livres)]
    if gained:  # subir de nível enche vida, chakra e energia
        player.vida_atual, player.chakra_atual, player.energia = player.vida_max, player.chakra_max, 25
    return gained  # o rank sobe pelos exames da Jornada (game_extra.py)


def min_rank_for(mission_rank: MissionRank) -> NinjaRank:
    """Menor patente que pode aceitar uma missão de determinado rank."""
    need = MISSION_ORDER.index(mission_rank)
    for ninja_rank in NinjaRank:  # ordem de definição: Estudante -> Kage
        if MISSION_ORDER.index(MAX_MISSION_RANK[ninja_rank]) >= need:
            return ninja_rank
    return NinjaRank.KAGE


def active_summon_link(player: Player) -> Optional[PlayerSummon]:
    """Retorna a invocação equipada (is_active) do jogador, se houver."""
    return next((ps for ps in player.player_summons if ps.is_active), None)


def effective_stats(player: Player) -> dict:
    """
    Atributos finais = base + passiva do clã (%) + itens equipados + pet ativo.
    Requer clan, inventory e player_summons já carregados.
    """
    stats = {a: getattr(player, a) for a in ATTRS}

    # 1) Passiva do clã: percentual sobre o valor base do atributo
    if player.clan:
        attr = player.clan.passiva_atributo.value
        stats[attr] += int(stats[attr] * player.clan.bonus_val)

    # 2) Itens equipados: status_bonus = {"taijutsu": 5, ...}
    for item in player.inventory:
        if item.is_equipped:
            for key, value in (item.status_bonus or {}).items():
                if key in stats and isinstance(value, (int, float)):
                    stats[key] += int(value)

    # 2b) Buff temporário do Hospital (recuperação Sannin)
    b = player.buff
    if b and b.get("attr") in stats and datetime.fromisoformat(b["ate"]) > datetime.now(timezone.utc):
        stats[b["attr"]] += int(stats[b["attr"]] * b["pct"])

    # 3) Invocação ativa
    link = active_summon_link(player)
    if link:
        mult = 1 + 0.2 * (link.level - 1)  # +20% do bônus por nível da invocação
        stats["taijutsu"] += int(link.summon.bonus_taijutsu * mult)
        stats["ninjutsu"] += int(link.summon.bonus_ninjutsu * mult)

    return stats


def as_utc(dt: datetime) -> datetime:
    """SQLite devolve datetimes sem fuso; normaliza para UTC aware."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ─────────────────────────────────────────────────────────────
# Schemas Pydantic
# ─────────────────────────────────────────────────────────────
class Attributes(BaseModel):
    taijutsu: int
    ninjutsu: int
    genjutsu: int
    agilidade: int
    forca: int
    inteligencia: int
    chakra_attr: int
    defesa: int
    vigor: int
    destreza: int


class Resource(BaseModel):
    atual: int
    max: int


class VillageOut(BaseModel):
    id: int
    name: str
    description: Optional[str] = None


class ClanOut(BaseModel):
    id: int
    name: str
    passiva_atributo: AttributeType
    bonus_val: float


class GuildOut(BaseModel):
    id: int
    name: str
    level: int


class SummonOut(BaseModel):
    id: int
    name: str
    level: int
    bonus_taijutsu: int
    bonus_ninjutsu: int


class PlayerOut(BaseModel):
    telegram_id: int
    username: Optional[str]
    ninja_name: str
    rank: NinjaRank
    level: int
    exp: int
    exp_proximo_nivel: int
    ryos: int
    fama: int
    energia: int
    avatar: str
    elemento: str
    elementos: list[str]
    chakra: Resource
    vida: Resource
    atributos_base: Attributes
    atributos_efetivos: Attributes
    village: VillageOut
    clan: ClanOut
    guild: Optional[GuildOut]
    pet_equipado: Optional[SummonOut]


class SetupOptions(BaseModel):
    villages: list[VillageOut]
    clans: list[ClanOut]
    teste: list[dict] = []


class RegisterRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    telegram_id: int = Field(gt=0)
    username: Optional[str] = Field(default=None, max_length=64)
    # Letras, números, _ e espaços (aceita acentos)
    ninja_name: str = Field(min_length=3, max_length=32, pattern=r"^[\w ]+$")
    village_id: int = Field(gt=0)
    # Estilo principal (+3) e 5 pontos para distribuir entre os 7 atributos de combate (todos começam em 5)
    estilo: Literal["taijutsu", "ninjutsu", "genjutsu"] = "taijutsu"
    agilidade: int = Field(default=0, ge=0, le=5)
    forca: int = Field(default=0, ge=0, le=5)
    inteligencia: int = Field(default=0, ge=0, le=5)
    chakra_attr: int = Field(default=0, ge=0, le=5)
    defesa: int = Field(default=0, ge=0, le=5)
    vigor: int = Field(default=0, ge=0, le=5)
    destreza: int = Field(default=0, ge=0, le=5)
    respostas: list[int] = Field(min_length=len(QUIZ), max_length=len(QUIZ))  # teste do elemento

    @model_validator(mode="after")
    def check_points(self):
        total = sum(getattr(self, k) for k in ("agilidade", "forca", "inteligencia", "chakra_attr", "defesa", "vigor", "destreza"))
        if total != 5:
            raise ValueError("Distribua exatamente 5 pontos entre os atributos.")
        if any(not 0 <= r <= 4 for r in self.respostas):
            raise ValueError("Respostas do teste inválidas.")
        return self


class TrainableAttribute(str, Enum):
    """Atributos que podem ser treinados."""
    TAIJUTSU = "taijutsu"
    NINJUTSU = "ninjutsu"
    AGILIDADE = "agilidade"
    GENJUTSU = "genjutsu"
    FORCA = "forca"
    INTELIGENCIA = "inteligencia"
    CHAKRA = "chakra_attr"
    DEFESA = "defesa"
    VIGOR = "vigor"
    DESTREZA = "destreza"


class TrainRequest(BaseModel):
    telegram_id: int = Field(gt=0)
    atributo: TrainableAttribute
    repeticoes: int = Field(default=1, ge=1, le=10)


class TrainResponse(BaseModel):
    atributo: TrainableAttribute
    novo_valor: int
    chakra_gasto: int
    ryos_gastos: int
    exp_ganha: int
    niveis_ganhos: int
    level: int
    exp: int
    chakra_atual: int
    ryos: int


class MissionStartRequest(BaseModel):
    telegram_id: int = Field(gt=0)
    mission_id: int = Field(gt=0)


class MissionStartResponse(BaseModel):
    player_mission_id: int
    mission_id: int
    title: str
    rank_required: MissionRank
    started_at: datetime
    ends_at: datetime
    duration_seconds: int


class MissionCompleteRequest(BaseModel):
    telegram_id: int = Field(gt=0)
    player_mission_id: int = Field(gt=0)
    # Escolha moral ao entregar: honra (+reputação) ou sombra (+30% de ryos, -reputação)
    escolha: Literal["honra", "sombra"] = "honra"


class MissionCompleteResponse(BaseModel):
    exp_ganha: int
    ryos_ganhos: int
    niveis_ganhos: int
    level: int
    exp: int
    ryos: int


class WarAttackRequest(BaseModel):
    telegram_id: int = Field(gt=0)
    target_guild_id: int = Field(gt=0)


class WarAttackResponse(BaseModel):
    vitoria: bool
    guilda_atacante: str
    guilda_defensora: str
    poder_ataque: float
    poder_defesa: float
    chakra_gasto: int
    dano_sofrido: int
    ryos_ganhos: int
    exp_ganha: int
    niveis_ganhos: int
    mensagem: str


# ─────────────────────────────────────────────────────────────
# Helpers de acesso a dados
# ─────────────────────────────────────────────────────────────
# ───────── Autenticação (initData assinado do Telegram) ─────────
# Defina BOT_TOKEN no servidor para ativar. Sem ele (desenvolvimento), a validação fica desligada.
BOT_TOKEN = os.getenv("BOT_TOKEN")
AUTH_ID: ContextVar = ContextVar("auth_id", default=None)


def verify_init_data(init_data: str, token: str, max_age: int = 86400):
    """Valida a assinatura HMAC do initData e devolve o id do usuário (ou None se inválido/expirado)."""
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        received = pairs.pop("hash", "")
        check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not received or not hmac.compare_digest(expected, received):
            return None
        if time.time() - int(pairs.get("auth_date", 0)) > max_age:
            return None
        return int(json.loads(pairs["user"])["id"])
    except (ValueError, KeyError, TypeError):
        return None


async def require_auth(x_telegram_init_data: str = Header(default="")):
    """Dependência global: só aceita requisições com initData válido e guarda o id autenticado."""
    if not BOT_TOKEN:
        return
    uid = verify_init_data(x_telegram_init_data, BOT_TOKEN)
    if uid is None:
        raise HTTPException(401, "Sessão do Telegram inválida ou expirada. Reabra o app pelo bot.")
    AUTH_ID.set(uid)


# ───────── Regeneração passiva ─────────
CHAKRA_REGEN_SECS = 100  # 3 de chakra a cada 5 min
ENERGY_REGEN_SECS = 180  # 1 de energia a cada 3 min (máximo 25)
HP_REGEN_SECS = 100      # 3 de vida a cada 5 min


def regen(player: Player) -> None:
    """Regenera vida/chakra conforme o tempo parado, calculado sob demanda (sem tarefa em segundo plano)."""
    now = datetime.now(timezone.utc)
    # Energia tem relógio próprio: preserva o resto do intervalo ao regenerar
    if player.energia_at is None:
        player.energia_at = now
    ticks = int((now - as_utc(player.energia_at)).total_seconds() // ENERGY_REGEN_SECS)
    if ticks:
        player.energia = min(25, player.energia + ticks)
        player.energia_at = now if player.energia >= 25 else as_utc(player.energia_at) + timedelta(seconds=ticks * ENERGY_REGEN_SECS)
    if player.regen_at is None:
        player.regen_at = now
        return
    secs = (now - as_utc(player.regen_at)).total_seconds()
    chakra, hp = int(secs // CHAKRA_REGEN_SECS), int(secs // HP_REGEN_SECS)
    if chakra:
        player.chakra_atual = min(player.chakra_max, player.chakra_atual + chakra)
    if hp:
        player.vida_atual = min(player.vida_max, player.vida_atual + hp)
    if chakra or hp:
        player.regen_at = now


def get_player(db: Session, telegram_id: int, lock: bool = False) -> Player:
    """
    Busca o jogador com tudo que as regras precisam (selectinload evita N+1).
    lock=True aplica SELECT ... FOR UPDATE na linha do jogador: impede que duas
    requisições simultâneas gastem o mesmo chakra/ryos (ignorado no SQLite).
    """
    if BOT_TOKEN and AUTH_ID.get() != telegram_id:
        raise HTTPException(403, "Você não pode agir em nome de outro jogador.")
    stmt = (
        select(Player)
        .where(Player.telegram_id == telegram_id)
        .options(
            selectinload(Player.village),
            selectinload(Player.clan),
            selectinload(Player.guild),
            selectinload(Player.inventory),
            selectinload(Player.player_summons).selectinload(PlayerSummon.summon),
        )
    )
    if lock:
        stmt = stmt.with_for_update(of=Player)
    player = db.execute(stmt).scalar_one_or_none()
    if player is None:
        raise HTTPException(status_code=404, detail="Jogador não encontrado.")
    regen(player)
    return player


def build_player_out(player: Player) -> PlayerOut:
    """Converte o ORM em resposta da API, incluindo atributos efetivos."""
    link = active_summon_link(player)
    pet = (
        SummonOut(
            id=link.summon.id,
            name=link.summon.name,
            level=link.level,
            bonus_taijutsu=link.summon.bonus_taijutsu,
            bonus_ninjutsu=link.summon.bonus_ninjutsu,
        )
        if link
        else None
    )
    return PlayerOut(
        telegram_id=player.telegram_id,
        username=player.username,
        ninja_name=player.ninja_name,
        rank=player.rank,
        level=player.level,
        exp=player.exp,
        exp_proximo_nivel=exp_for_level(player.level + 1),
        ryos=player.ryos,
        fama=player.fama,
        energia=player.energia,
        avatar=player.avatar,
        elemento=player.elemento,
        elementos=[player.elemento, *(player.elementos_extra or [])],
        chakra=Resource(atual=player.chakra_atual, max=player.chakra_max),
        vida=Resource(atual=player.vida_atual, max=player.vida_max),
        atributos_base=Attributes(**{a: getattr(player, a) for a in ATTRS}),
        atributos_efetivos=Attributes(**effective_stats(player)),
        village=VillageOut(
            id=player.village.id,
            name=player.village.name,
            description=player.village.description,
        ),
        clan=ClanOut(
            id=player.clan.id,
            name=player.clan.name,
            passiva_atributo=player.clan.passiva_atributo,
            bonus_val=player.clan.bonus_val,
        ),
        guild=(
            GuildOut(id=player.guild.id, name=player.guild.name, level=player.guild.level)
            if player.guild
            else None
        ),
        pet_equipado=pet,
    )


# ─────────────────────────────────────────────────────────────
# Dados iniciais (apenas para desenvolvimento/teste)
# ─────────────────────────────────────────────────────────────
def seed_initial_data() -> None:
    """Cria vilas, clãs e missões de exemplo se o banco estiver vazio."""
    with SessionLocal() as db:
        if db.scalar(select(Village.id).limit(1)) is not None:
            return
        db.add_all([
            *[Village(name=v["nome"], npc_kage=v["kage"], description=f'{v["historia"]} Kage: {v["kage"]}.') for v in VILAS],
            Clan(name="Sem clã", passiva_atributo=AttributeType.TAIJUTSU, bonus_val=0.0),
            *[Clan(name=c[1], passiva_atributo=AttributeType(c[2]), bonus_val=c[3]) for c in CLANS],
            Summon(name="Grande Sapo Guerreiro", level=1, bonus_taijutsu=3, bonus_ninjutsu=1),
            Summon(name="Serpente Abissal", level=1, bonus_taijutsu=1, bonus_ninjutsu=3),
            Mission(title="Recuperar o gato fujão", rank_required=MissionRank.D, exp_reward=20, ryos_reward=50, duration_seconds=60),
            Mission(title="Escoltar um mercador", rank_required=MissionRank.C, exp_reward=60, ryos_reward=150, duration_seconds=300),
            Mission(title="Caçar bandidos da estrada", rank_required=MissionRank.B, exp_reward=150, ryos_reward=400, duration_seconds=900),
            Mission(title="Infiltrar o forte inimigo", rank_required=MissionRank.A, exp_reward=400, ryos_reward=1000, duration_seconds=1800),
            Mission(title="Derrotar o ninja renegado", rank_required=MissionRank.S, exp_reward=1000, ryos_reward=3000, duration_seconds=3600),
        ])
        db.commit()


# ─────────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────────
def ensure_columns() -> None:
    """Migração leve: o create_all não altera tabelas existentes, então colunas novas do models.py são adicionadas aqui."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    for table in Base.metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue
        have = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in have:
                continue
            arg = col.default.arg if col.default is not None else None
            if callable(arg):  # o SQLAlchemy embrulha defaults como dict/list numa função que recebe o contexto
                try:
                    arg = arg(None)
                except TypeError:
                    arg = arg()
            lit = "NULL" if arg is None else f"'{json.dumps(arg)}'" if isinstance(arg, (dict, list)) else "1" if arg is True else "0" if arg is False else f"'{arg}'" if isinstance(arg, str) else str(arg)
            with engine.begin() as con:
                con.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col.type.compile(engine.dialect)} DEFAULT {lit}'))


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Em produção prefira migrações (Alembic) a create_all
    Base.metadata.create_all(bind=engine)
    ensure_columns()
    seed_initial_data()
    yield


app = FastAPI(title="RPG Ninja API", version="1.0.0", lifespan=lifespan, dependencies=[Depends(require_auth)])

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(SQLAlchemyError)
async def db_error_handler(_: Request, exc: SQLAlchemyError):
    """Qualquer erro de banco não tratado vira 500 genérico (sem vazar detalhes)."""
    logger.exception("Erro de banco de dados: %s", exc)
    return JSONResponse(status_code=500, content={"detail": "Erro interno ao acessar o banco de dados."})


# ─────────────────────────────────────────────────────────────
# Rotas
# ─────────────────────────────────────────────────────────────
@app.get("/api/setup/options", response_model=SetupOptions)
def setup_options(db: Session = Depends(get_db)):
    """Lista vilas e clãs para a tela de criação de personagem."""
    villages = db.scalars(select(Village).order_by(Village.name)).all()
    clans = db.scalars(select(Clan).order_by(Clan.name)).all()
    return SetupOptions(
        villages=[VillageOut(id=v.id, name=v.name, description=v.description) for v in villages],
        clans=[
            ClanOut(id=c.id, name=c.name, passiva_atributo=c.passiva_atributo, bonus_val=c.bonus_val)
            for c in clans
        ],
        teste=[{"p": q["p"], "o": [t for t, _ in q["o"]]} for q in QUIZ],
    )


# 1) Cadastro ──────────────────────────────────────────────────
@app.post("/api/register", response_model=PlayerOut, status_code=201)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    """Cria o perfil: nome, vila e 5 pontos de atributo. Começa sem clã, como Estudante, nível 1, com 500 ryos."""
    if BOT_TOKEN and AUTH_ID.get() != payload.telegram_id:
        raise HTTPException(403, "Você não pode criar personagem para outro usuário.")
    if db.scalar(select(Player.id).where(Player.telegram_id == payload.telegram_id)):
        raise HTTPException(409, "Este usuário do Telegram já possui um personagem.")
    if db.scalar(select(Player.id).where(Player.ninja_name == payload.ninja_name)):
        raise HTTPException(409, "Este nome ninja já está em uso.")
    if db.get(Village, payload.village_id) is None:
        raise HTTPException(404, "Vila não encontrada.")
    no_clan = db.scalar(select(Clan).where(Clan.name == NO_CLAN_NAME))
    if no_clan is None:
        raise HTTPException(500, "Dados iniciais ausentes (clã padrão).")

    # Valores iniciais (rank, level, exp, ryos, chakra, vida, atributos) vêm dos defaults do model
    db.add(Player(
        telegram_id=payload.telegram_id,
        username=payload.username,
        ninja_name=payload.ninja_name,
        village_id=payload.village_id,
        clan_id=no_clan.id,
        taijutsu=5 + (3 if payload.estilo == "taijutsu" else 0),
        ninjutsu=5 + (3 if payload.estilo == "ninjutsu" else 0),
        genjutsu=5 + (3 if payload.estilo == "genjutsu" else 0),
        agilidade=5 + payload.agilidade, forca=5 + payload.forca, inteligencia=5 + payload.inteligencia,
        chakra_attr=5 + payload.chakra_attr, defesa=5 + payload.defesa, vigor=5 + payload.vigor,
        destreza=5 + payload.destreza, elemento=elemento_do_teste(payload.respostas),
        vida_max=100 + 5 * payload.vigor, vida_atual=100 + 5 * payload.vigor,
        chakra_max=100 + 4 * payload.chakra_attr, chakra_atual=100 + 4 * payload.chakra_attr,
    ))
    try:
        db.commit()
    except IntegrityError:
        # Corrida: outra requisição criou o mesmo telegram_id/nome entre a checagem e o commit
        db.rollback()
        raise HTTPException(409, "Personagem ou nome ninja já existe.")

    return build_player_out(get_player(db, payload.telegram_id))


# 2) Perfil ────────────────────────────────────────────────────
@app.get("/api/player/{telegram_id}", response_model=PlayerOut)
def read_player(telegram_id: int, db: Session = Depends(get_db)):
    """Perfil completo: atributos (base e efetivos), rank, vila, clã, guilda e pet equipado."""
    if telegram_id <= 0:
        raise HTTPException(422, "telegram_id inválido.")
    return build_player_out(get_player(db, telegram_id))


# 3) Treino ────────────────────────────────────────────────────
@app.post("/api/train", response_model=TrainResponse)
def train(payload: TrainRequest, db: Session = Depends(get_db)):
    """Gasta chakra e ryos por repetição para subir um atributo e ganhar EXP."""
    player = get_player(db, payload.telegram_id, lock=True)

    chakra_cost = TRAIN_CHAKRA_COST * payload.repeticoes
    ryos_cost = TRAIN_RYOS_COST * payload.repeticoes
    exp_gain = TRAIN_EXP_GAIN * payload.repeticoes

    if player.chakra_atual < chakra_cost:
        raise HTTPException(400, f"Chakra insuficiente: precisa de {chakra_cost}, tem {player.chakra_atual}.")
    if player.ryos < ryos_cost:
        raise HTTPException(400, f"Ryos insuficientes: precisa de {ryos_cost}, tem {player.ryos}.")

    player.chakra_atual -= chakra_cost
    player.ryos -= ryos_cost
    attr = payload.atributo.value
    setattr(player, attr, getattr(player, attr) + TRAIN_ATTR_GAIN * payload.repeticoes)
    if attr == "vigor":  # cada ponto de vigor aumenta a vida máxima
        player.vida_max += 5 * payload.repeticoes
        player.vida_atual += 5 * payload.repeticoes
    elif attr == "chakra_attr":  # cada ponto de chakra aumenta a reserva
        player.chakra_max += 4 * payload.repeticoes
        player.chakra_atual += 4 * payload.repeticoes
    levels = apply_exp(player, exp_gain)
    db.commit()

    return TrainResponse(
        atributo=payload.atributo,
        novo_valor=getattr(player, attr),
        chakra_gasto=chakra_cost,
        ryos_gastos=ryos_cost,
        exp_ganha=exp_gain,
        niveis_ganhos=levels,
        level=player.level,
        exp=player.exp,
        chakra_atual=player.chakra_atual,
        ryos=player.ryos,
    )


# 4) Missões ───────────────────────────────────────────────────
@app.post("/api/mission/start", response_model=MissionStartResponse)
def start_mission(payload: MissionStartRequest, db: Session = Depends(get_db)):
    """Inicia uma missão se o rank do jogador permitir e não houver outra em andamento."""
    player = get_player(db, payload.telegram_id, lock=True)
    mission = db.get(Mission, payload.mission_id)
    if mission is None:
        raise HTTPException(404, "Missão não encontrada.")

    # Rank mínimo: compara a posição da missão (D..S) com o teto da patente do jogador
    allowed = MISSION_ORDER.index(MAX_MISSION_RANK[player.rank])
    required = MISSION_ORDER.index(mission.rank_required)
    if required > allowed:
        raise HTTPException(
            403,
            f"Rank insuficiente: missões {mission.rank_required.value} exigem "
            f"ao menos {min_rank_for(mission.rank_required).value} (você é {player.rank.value}).",
        )
    if player.vida_atual <= 0:
        raise HTTPException(400, "Você está sem vida para iniciar uma missão.")

    in_progress = db.scalar(
        select(PlayerMission.id).where(
            PlayerMission.player_id == player.id,
            PlayerMission.status == MissionStatus.EM_ANDAMENTO,
        )
    )
    if in_progress:
        raise HTTPException(409, "Você já tem uma missão em andamento.")

    now = datetime.now(timezone.utc)
    pm = PlayerMission(
        player_id=player.id,
        mission_id=mission.id,
        started_at=now,
        ends_at=now + timedelta(seconds=mission.duration_seconds),
    )
    db.add(pm)
    db.commit()

    return MissionStartResponse(
        player_mission_id=pm.id,
        mission_id=mission.id,
        title=mission.title,
        rank_required=mission.rank_required,
        started_at=pm.started_at,
        ends_at=pm.ends_at,
        duration_seconds=mission.duration_seconds,
    )


@app.post("/api/mission/complete", response_model=MissionCompleteResponse)
def complete_mission(payload: MissionCompleteRequest, db: Session = Depends(get_db)):
    """Coleta a recompensa de uma missão cujo tempo já terminou."""
    player = get_player(db, payload.telegram_id, lock=True)
    pm = db.get(PlayerMission, payload.player_mission_id)
    if pm is None or pm.player_id != player.id:
        raise HTTPException(404, "Missão do jogador não encontrada.")
    if pm.status != MissionStatus.EM_ANDAMENTO:
        raise HTTPException(409, "Esta missão já foi concluída.")

    remaining = (as_utc(pm.ends_at) - datetime.now(timezone.utc)).total_seconds()
    if remaining > 0:
        raise HTTPException(409, f"A missão ainda não terminou: faltam {int(remaining) + 1}s.")

    mission = db.get(Mission, pm.mission_id)
    pm.status = MissionStatus.CONCLUIDA
    pm.finished_at = datetime.now(timezone.utc)
    shadow = payload.escolha == "sombra"
    ryos = int(mission.ryos_reward * (1.3 if shadow else 1))
    player.ryos += ryos
    player.reputation = max(-100, min(100, player.reputation + (-8 if shadow else 5)))
    levels = apply_exp(player, mission.exp_reward)
    db.commit()

    return MissionCompleteResponse(
        exp_ganha=mission.exp_reward,
        ryos_ganhos=ryos,
        niveis_ganhos=levels,
        level=player.level,
        exp=player.exp,
        ryos=player.ryos,
    )


# 5) Guerra de guildas ─────────────────────────────────────────
@app.post("/api/guild/war/attack", response_model=WarAttackResponse)
def guild_war_attack(payload: WarAttackRequest, db: Session = Depends(get_db)):
    """
    Duelo simplificado: ataque do jogador vs defesa média dos melhores defensores da guilda alvo.

      ataque  = (taijutsu + ninjutsu + agilidade/2) * bônus de nível da guilda
      defesa  = média dos N melhores de (agilidade + taijutsu/2 + vida_max/25) * bônus da guilda
      Ambos recebem um fator de sorte; ataque > defesa = vitória.
    """
    attacker = get_player(db, payload.telegram_id, lock=True)

    if attacker.guild is None:
        raise HTTPException(403, "Você precisa estar em uma guilda para atacar.")
    if attacker.guilda_id == payload.target_guild_id:
        raise HTTPException(400, "Não é possível atacar a própria guilda.")
    target = db.get(Guild, payload.target_guild_id)
    if target is None:
        raise HTTPException(404, "Guilda alvo não encontrada.")
    if attacker.vida_atual <= 0:
        raise HTTPException(400, "Você está sem vida para lutar.")
    if attacker.chakra_atual < WAR_CHAKRA_COST:
        raise HTTPException(400, f"Chakra insuficiente: precisa de {WAR_CHAKRA_COST}, tem {attacker.chakra_atual}.")

    # Pré-filtra no SQL os 20 membros mais ágeis (evita carregar guildas enormes);
    # o cálculo final de defesa usa os atributos efetivos desses candidatos.
    defenders = db.scalars(
        select(Player)
        .where(Player.guilda_id == target.id)
        .order_by(Player.agilidade.desc(), Player.taijutsu.desc())
        .limit(20)
        .options(
            selectinload(Player.clan),
            selectinload(Player.inventory),
            selectinload(Player.player_summons).selectinload(PlayerSummon.summon),
        )
    ).all()
    if not defenders:
        raise HTTPException(409, "A guilda alvo não tem defensores.")

    # Defesa da guilda alvo
    def_scores = []
    for d in defenders:
        s = effective_stats(d)
        def_scores.append(s["agilidade"] + s["taijutsu"] / 2 + d.vida_max / 25)
    top = sorted(def_scores, reverse=True)[:WAR_DEFENDERS_COUNT]
    defense = (sum(top) / len(top)) * (1 + 0.02 * (target.level - 1))

    # Ataque do jogador
    a = effective_stats(attacker)
    attack = (a["taijutsu"] + a["ninjutsu"] + a["agilidade"] / 2) * (1 + 0.02 * (attacker.guild.level - 1))

    attack_roll = attack * random.uniform(*WAR_ROLL_RANGE)
    defense_roll = defense * random.uniform(*WAR_ROLL_RANGE)
    victory = attack_roll > defense_roll

    attacker.chakra_atual -= WAR_CHAKRA_COST
    damage = 0
    ryos_gain = 0
    if victory:
        ryos_gain = 50 * target.level
        exp_gain = 40
        attacker.ryos += ryos_gain
        # Expressão SQL: incremento atômico, sem "lost update" entre jogadores da mesma guilda
        attacker.guild.vitorias_guerra = Guild.vitorias_guerra + 1
        message = f"Vitória! Sua guilda {attacker.guild.name} levou a melhor sobre {target.name}."
    else:
        exp_gain = 10  # consolo
        damage = min(WAR_LOSS_DAMAGE, attacker.vida_atual - 1)  # nunca morre: mínimo 1 de vida
        attacker.vida_atual -= damage
        message = f"Derrota. A defesa de {target.name} resistiu ao seu ataque."

    levels = apply_exp(attacker, exp_gain)
    attacker_guild_name = attacker.guild.name
    db.commit()

    return WarAttackResponse(
        vitoria=victory,
        guilda_atacante=attacker_guild_name,
        guilda_defensora=target.name,
        poder_ataque=round(attack_roll, 2),
        poder_defesa=round(defense_roll, 2),
        chakra_gasto=WAR_CHAKRA_COST,
        dano_sofrido=damage,
        ryos_ganhos=ryos_gain,
        exp_ganha=exp_gain,
        niveis_ganhos=levels,
        mensagem=message,
    )


# Rotas extras (mapas, batalha PvE, inventário, invocações, clãs, descanso).
# Import no fim do arquivo de propósito: game_extra reutiliza helpers definidos acima.
from game_extra import router as extra_router  # noqa: E402

app.include_router(extra_router)
