"""
models.py — Modelagem do banco de dados do RPG Ninja (Telegram Mini App)
SQLAlchemy 2.0 (ORM declarativo tipado).

Diagrama resumido:
    Village 1─N Player N─1 Clan
    Guild   1─N Player
    Player  N─M Summon   (via PlayerSummon, tabela de associação com dados extras)
    Player  1─N InventoryItem
    Mission (catálogo independente)

Observação sobre dependências circulares:
    Village.leader_kage_id -> players.id  e  Player.village_id -> villages.id
    Guild.leader_id        -> players.id  e  Player.guilda_id -> guilds.id
    Para evitar erro na criação das tabelas, essas FKs usam `use_alter=True`
    e os relacionamentos usam `post_update=True`.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


# ─────────────────────────────────────────────────────────────
# Base e mixins
# ─────────────────────────────────────────────────────────────
class Base(DeclarativeBase):
    """Classe base de todos os modelos."""


class TimestampMixin:
    """Adiciona created_at / updated_at automáticos."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# ─────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────
class NinjaRank(str, enum.Enum):
    """Patente do jogador."""
    ESTUDANTE = "Estudante"
    GENIN = "Genin"
    CHUNIN = "Chunin"
    JONIN = "Jonin"
    ANBU = "Anbu"
    KAGE = "Kage"


class MissionRank(str, enum.Enum):
    """Dificuldade da missão (D = mais fácil, S = mais difícil)."""
    D = "D"
    C = "C"
    B = "B"
    A = "A"
    S = "S"


class AttributeType(str, enum.Enum):
    """Atributos que um clã pode potencializar passivamente."""
    TAIJUTSU = "taijutsu"
    NINJUTSU = "ninjutsu"
    GENJUTSU = "genjutsu"
    AGILIDADE = "agilidade"


class ItemType(str, enum.Enum):
    """Categoria do item de inventário."""
    ARMA = "Arma"
    ARMADURA = "Armadura"
    PERGAMINHO = "Pergaminho"


# ─────────────────────────────────────────────────────────────
# Tabela de associação Player <-> Summon (N:M)
# ─────────────────────────────────────────────────────────────
class PlayerSummon(Base):
    """
    Invocações que o jogador possui.
    Usamos um modelo (e não uma Table simples) para guardar dados extras,
    como o nível individual da invocação e se está ativa.
    """
    __tablename__ = "player_summons"

    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), primary_key=True
    )
    summon_id: Mapped[int] = mapped_column(
        ForeignKey("summons.id", ondelete="CASCADE"), primary_key=True
    )
    level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=False, nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    player: Mapped["Player"] = relationship(back_populates="player_summons")
    summon: Mapped["Summon"] = relationship(back_populates="player_summons")


# ─────────────────────────────────────────────────────────────
# 1. Player
# ─────────────────────────────────────────────────────────────
class Player(TimestampMixin, Base):
    """Jogador (usuário do Telegram + ficha de personagem ninja)."""
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Identidade
    telegram_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, index=True, nullable=False
    )
    username: Mapped[Optional[str]] = mapped_column(String(64))  # @username pode não existir
    ninja_name: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)

    # Progressão
    rank: Mapped[NinjaRank] = mapped_column(
        Enum(NinjaRank, name="ninja_rank"),
        default=NinjaRank.ESTUDANTE,
        nullable=False,
    )
    level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    exp: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    ryos: Mapped[int] = mapped_column(BigInteger, default=500, nullable=False)
    # Reputação: -100 (renegado) a +100 (herói da vila)
    reputation: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Jornada: missões concluídas e fim da missão em andamento (se houver)
    journey: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    journey_ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Recursos de combate
    chakra_max: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    chakra_atual: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    vida_max: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    vida_atual: Mapped[int] = mapped_column(Integer, default=100, nullable=False)

    # Atributos base
    taijutsu: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    ninjutsu: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    genjutsu: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    agilidade: Mapped[int] = mapped_column(Integer, default=5, nullable=False)

    # Chaves estrangeiras
    village_id: Mapped[int] = mapped_column(
        ForeignKey("villages.id", use_alter=True, name="fk_players_village"),
        nullable=False,
        index=True,
    )
    clan_id: Mapped[int] = mapped_column(
        ForeignKey("clans.id"), nullable=False, index=True
    )
    guilda_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("guilds.id", use_alter=True, name="fk_players_guild", ondelete="SET NULL"),
        index=True,
    )

    # Relacionamentos
    village: Mapped["Village"] = relationship(
        back_populates="members", foreign_keys=[village_id]
    )
    clan: Mapped["Clan"] = relationship(back_populates="members")
    guild: Mapped[Optional["Guild"]] = relationship(
        back_populates="members", foreign_keys=[guilda_id]
    )
    inventory: Mapped[list["InventoryItem"]] = relationship(
        back_populates="player", cascade="all, delete-orphan"
    )
    player_summons: Mapped[list["PlayerSummon"]] = relationship(
        back_populates="player", cascade="all, delete-orphan"
    )

    # Atalho N:M: player.summons -> lista de Summon (somente leitura)
    summons: Mapped[list["Summon"]] = relationship(
        secondary="player_summons", viewonly=True
    )

    __table_args__ = (
        # Garante integridade dos valores de combate
        CheckConstraint("level >= 1", name="ck_player_level_min"),
        CheckConstraint("exp >= 0", name="ck_player_exp_min"),
        CheckConstraint("ryos >= 0", name="ck_player_ryos_min"),
        CheckConstraint("chakra_atual >= 0 AND chakra_atual <= chakra_max", name="ck_player_chakra"),
        CheckConstraint("vida_atual >= 0 AND vida_atual <= vida_max", name="ck_player_vida"),
        # Índice composto útil para rankings
        Index("ix_players_level_exp", "level", "exp"),
    )

    def __repr__(self) -> str:
        return f"<Player {self.ninja_name!r} lv{self.level} {self.rank.value}>"


# ─────────────────────────────────────────────────────────────
# 2. Village
# ─────────────────────────────────────────────────────────────
class Village(Base):
    """Vila ninja (ex.: Vila do Vento, Vila das Sombras)."""
    __tablename__ = "villages"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)

    # Kage = jogador que lidera a vila (opcional; a vila pode estar sem líder)
    leader_kage_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("players.id", use_alter=True, name="fk_villages_kage", ondelete="SET NULL"),
        unique=True,  # um jogador só pode ser Kage de uma vila
    )

    leader_kage: Mapped[Optional["Player"]] = relationship(
        foreign_keys=[leader_kage_id], post_update=True
    )
    members: Mapped[list["Player"]] = relationship(
        back_populates="village", foreign_keys="Player.village_id"
    )

    def __repr__(self) -> str:
        return f"<Village {self.name!r}>"


# ─────────────────────────────────────────────────────────────
# 3. Clan
# ─────────────────────────────────────────────────────────────
class Clan(Base):
    """Clã (ex.: Clã das Chamas, Clã dos Olhos Astrais) com bônus passivo."""
    __tablename__ = "clans"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # Atributo que o clã potencializa e o valor do bônus (ex.: 0.10 = +10%)
    passiva_atributo: Mapped[AttributeType] = mapped_column(
        Enum(AttributeType, name="attribute_type"), nullable=False
    )
    bonus_val: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    members: Mapped[list["Player"]] = relationship(back_populates="clan")

    def __repr__(self) -> str:
        return f"<Clan {self.name!r} +{self.bonus_val} {self.passiva_atributo.value}>"


# ─────────────────────────────────────────────────────────────
# 4. Guild
# ─────────────────────────────────────────────────────────────
class Guild(TimestampMixin, Base):
    """Guilda / organização de jogadores."""
    __tablename__ = "guilds"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    leader_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", use_alter=True, name="fk_guilds_leader"),
        nullable=False,
        index=True,
    )
    level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    recursos_doados: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    vitorias_guerra: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    leader: Mapped["Player"] = relationship(
        foreign_keys=[leader_id], post_update=True
    )
    members: Mapped[list["Player"]] = relationship(
        back_populates="guild", foreign_keys="Player.guilda_id"
    )

    __table_args__ = (
        CheckConstraint("level >= 1", name="ck_guild_level_min"),
        CheckConstraint("recursos_doados >= 0", name="ck_guild_recursos_min"),
    )

    def __repr__(self) -> str:
        return f"<Guild {self.name!r} lv{self.level}>"


# ─────────────────────────────────────────────────────────────
# 5. Summon
# ─────────────────────────────────────────────────────────────
class Summon(Base):
    """Invocação / pet (ex.: Grande Sapo Guerreiro, Serpente Abissal)."""
    __tablename__ = "summons"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    bonus_taijutsu: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    bonus_ninjutsu: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    player_summons: Mapped[list["PlayerSummon"]] = relationship(
        back_populates="summon", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Summon {self.name!r} lv{self.level}>"


# ─────────────────────────────────────────────────────────────
# 6. Mission
# ─────────────────────────────────────────────────────────────
class Mission(Base):
    """Missão disponível no jogo (catálogo)."""
    __tablename__ = "missions"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    rank_required: Mapped[MissionRank] = mapped_column(
        Enum(MissionRank, name="mission_rank"), nullable=False, index=True
    )
    exp_reward: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    ryos_reward: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint("duration_seconds > 0", name="ck_mission_duration"),
        CheckConstraint("exp_reward >= 0 AND ryos_reward >= 0", name="ck_mission_rewards"),
    )

    def __repr__(self) -> str:
        return f"<Mission {self.title!r} [{self.rank_required.value}]>"


# ─────────────────────────────────────────────────────────────
# 7. InventoryItem (PlayerEquipment)
# ─────────────────────────────────────────────────────────────
class InventoryItem(Base):
    """Item do inventário/equipamento do jogador."""
    __tablename__ = "inventory_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_name: Mapped[str] = mapped_column(String(64), nullable=False)
    item_type: Mapped[ItemType] = mapped_column(
        Enum(ItemType, name="item_type"), nullable=False
    )
    # Bônus flexível em JSON, ex.: {"taijutsu": 5, "agilidade": 2}
    status_bonus: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    is_equipped: Mapped[bool] = mapped_column(default=False, nullable=False)

    player: Mapped["Player"] = relationship(back_populates="inventory")

    def __repr__(self) -> str:
        return f"<InventoryItem {self.item_name!r} ({self.item_type.value})>"


# ─────────────────────────────────────────────────────────────
# 8. PlayerMission (missões em andamento / concluídas)
# ─────────────────────────────────────────────────────────────
class MissionStatus(str, enum.Enum):
    """Estado de uma missão aceita pelo jogador."""
    EM_ANDAMENTO = "em_andamento"
    CONCLUIDA = "concluida"


class PlayerMission(Base):
    """
    Registro de uma missão iniciada por um jogador.
    `ends_at` = started_at + duration_seconds da missão; a recompensa só é
    entregue quando o jogador coleta (POST /api/mission/complete).
    """
    __tablename__ = "player_missions"

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )
    mission_id: Mapped[int] = mapped_column(
        ForeignKey("missions.id"), nullable=False
    )
    status: Mapped[MissionStatus] = mapped_column(
        Enum(MissionStatus, name="mission_status"),
        default=MissionStatus.EM_ANDAMENTO,
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    player: Mapped["Player"] = relationship()
    mission: Mapped["Mission"] = relationship()

    __table_args__ = (Index("ix_player_missions_player_status", "player_id", "status"),)
