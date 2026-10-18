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

import logging
import os
import random
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Iterator, Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
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
ATTRS = ("taijutsu", "ninjutsu", "genjutsu", "agilidade")

# Promoção automática de rank por nível (Kage é concedido à parte)
RANK_BY_LEVEL = [(1, NinjaRank.ESTUDANTE), (5, NinjaRank.GENIN), (12, NinjaRank.CHUNIN),
                 (20, NinjaRank.JONIN), (30, NinjaRank.ANBU)]

# Todo jogador nasce neste "clã" vazio e troca ao passar no teste de admissão
NO_CLAN_NAME = "Sem clã"

# Bônus da classe inicial escolhida no cadastro
CLASS_START = {
    "taijutsu": {"vida_max": 130, "vida_atual": 130, "taijutsu": 8},
    "ninjutsu": {"chakra_max": 130, "chakra_atual": 130, "ninjutsu": 8},
    "genjutsu": {"agilidade": 8, "genjutsu": 8},
}

# Ordem de dificuldade das missões e rank máximo de missão que cada patente pode aceitar
MISSION_ORDER = [MissionRank.D, MissionRank.C, MissionRank.B, MissionRank.A, MissionRank.S]
MAX_MISSION_RANK = {
    NinjaRank.ESTUDANTE: MissionRank.D,
    NinjaRank.GENIN: MissionRank.C,
    NinjaRank.CHUNIN: MissionRank.B,
    NinjaRank.JONIN: MissionRank.A,
    NinjaRank.ANBU: MissionRank.S,
    NinjaRank.KAGE: MissionRank.S,
}

# Treino (custo e ganho por repetição)
TRAIN_CHAKRA_COST = 10
TRAIN_RYOS_COST = 25
TRAIN_ATTR_GAIN = 1
TRAIN_EXP_GAIN = 15

# Guerra de guildas
WAR_CHAKRA_COST = 20
WAR_DEFENDERS_COUNT = 5      # só os N melhores defensores contam
WAR_LOSS_DAMAGE = 15         # vida perdida pelo atacante ao perder
WAR_ROLL_RANGE = (0.85, 1.15)  # fator de sorte aplicado a ataque e defesa


def exp_for_level(level: int) -> int:
    """EXP total necessária para ALCANÇAR `level` (nível 1 = 0, nível 2 = 100, nível 3 = 300...)."""
    return 50 * level * (level - 1)


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


class RegisterRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    telegram_id: int = Field(gt=0)
    username: Optional[str] = Field(default=None, max_length=64)
    # Letras, números, _ e espaços (aceita acentos)
    ninja_name: str = Field(min_length=3, max_length=32, pattern=r"^[\w ]+$")
    village_id: int = Field(gt=0)
    classe: Literal["taijutsu", "ninjutsu", "genjutsu"] = "taijutsu"


class TrainableAttribute(str, Enum):
    """Atributos que podem ser treinados."""
    TAIJUTSU = "taijutsu"
    NINJUTSU = "ninjutsu"
    AGILIDADE = "agilidade"
    GENJUTSU = "genjutsu"


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
def get_player(db: Session, telegram_id: int, lock: bool = False) -> Player:
    """
    Busca o jogador com tudo que as regras precisam (selectinload evita N+1).
    lock=True aplica SELECT ... FOR UPDATE na linha do jogador: impede que duas
    requisições simultâneas gastem o mesmo chakra/ryos (ignorado no SQLite).
    """
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
            Village(name="Vila das Chamas Eternas", description="Especialistas em combate ofensivo e determinação."),
            Village(name="Vila do Vento Prateado", description="Focados em velocidade, agilidade e esquiva."),
            Village(name="Vila da Névoa Oculta", description="Mestres no assassinato silencioso e nas táticas."),
            Clan(name="Sem clã", passiva_atributo=AttributeType.TAIJUTSU, bonus_val=0.0),
            Clan(name="Clã do Olho Astral", passiva_atributo=AttributeType.GENJUTSU, bonus_val=0.20),
            Clan(name="Clã das Sombras Abissais", passiva_atributo=AttributeType.AGILIDADE, bonus_val=0.15),
            Clan(name="Clã das Chamas", passiva_atributo=AttributeType.NINJUTSU, bonus_val=0.10),
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
@asynccontextmanager
async def lifespan(_: FastAPI):
    # Em produção prefira migrações (Alembic) a create_all
    Base.metadata.create_all(bind=engine)
    seed_initial_data()
    yield


app = FastAPI(title="RPG Ninja API", version="1.0.0", lifespan=lifespan)

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
    )


# 1) Cadastro ──────────────────────────────────────────────────
@app.post("/api/register", response_model=PlayerOut, status_code=201)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    """Cria o perfil: nome, classe e vila. Começa sem clã, como Estudante, nível 1, com 500 ryos."""
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
        **CLASS_START[payload.classe],
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
