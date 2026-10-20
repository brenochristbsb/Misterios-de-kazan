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
    Boolean,
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
    SANNIN = "Sannin"  # só 3 no mundo; só eles disputam o cargo de Kage
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
    FORCA = "forca"
    INTELIGENCIA = "inteligencia"
    CHAKRA_ATTR = "chakra_attr"
    DEFESA = "defesa"
    VIGOR = "vigor"
    DESTREZA = "destreza"


class ItemType(str, enum.Enum):
    """Categoria do item de inventário."""
    ARMA = "Arma"
    ARMADURA = "Armadura"
    PERGAMINHO = "Pergaminho"
    BOTAS = "Botas"
    LUVAS = "Luvas"
    COLAR = "Colar"
    ANEL = "Anel"


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
    # Última vez em que vida/chakra foram regenerados (cálculo sob demanda)
    regen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # Fama (ranking e disputa do cargo de Kage), energia de exploração (0 a 25), avatar e progresso da missão atual
    fama: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    energia: Mapped[int] = mapped_column(Integer, default=25, nullable=False)
    energia_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    avatar: Mapped[str] = mapped_column(String(8), default="🥷", nullable=False)
    journey_prog: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

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
    # Atributos de combate: força, inteligência (controle de chakra e missões secretas), reserva de chakra,
    # defesa, vigor (vida) e destreza (armas e crítico)
    forca: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    inteligencia: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    chakra_attr: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    defesa: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    vigor: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    destreza: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    # Luta por turnos em andamento (inimigo, vida, chakra e log); None quando não há luta
    luta: Mapped[Optional[dict]] = mapped_column(JSON)
    # Início do período em que este Anbu supera em fama o Sannin mais fraco (7 dias seguidos tomam a cadeira)
    sannin_desde: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # Degrau de elite: Jonin de Elite e Anbu de Elite. A patente-base segue Jonin/Anbu, então o enum do banco não muda
    elite: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Provação de ingresso em um clã (etapa atual e progresso dela) e pontos de clã
    prov_clan_id: Mapped[Optional[int]] = mapped_column(Integer)
    prov_etapa: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    prov_prog: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    clan_pontos: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Caminho do líder: reliquia, poder ou segredo; técnica secreta aprendida
    caminho: Mapped[Optional[str]] = mapped_column(String(12))
    caminho_prog: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    caminho_ok: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    tecnica: Mapped[Optional[str]] = mapped_column(String(48))
    # Poções em estoque: {id da poção: quantidade}
    pocoes: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # Hospital: última cura gratuita e buff temporário da recuperação Sannin {nome, attr, pct, ate}
    hospital_gratis_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    buff: Mapped[Optional[dict]] = mapped_column(JSON)
    # Elementos: base (do teste da criação) e os dominados depois; jutsus escolhidos {nível: id do jutsu}
    elemento: Mapped[str] = mapped_column(String(8), default="fogo", nullable=False)
    elementos_extra: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    jutsus: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # Moral (0 a 25): +1 por vitória, zera ao morrer; só aumenta a chance de drops melhores
    moral: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Vitórias por tipo de inimigo {nome: n} (30 liberam o combate rápido) e status negativos que seguem fora de luta
    abates: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status_fora: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    # Ovos e alimentos de pet em estoque: {id: quantidade}
    ovos: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    alimentos: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # Forja: materiais em estoque {id: quantidade} (não ocupam a mochila). Dojo: pontos de maestria por atributo {atributo: pontos}
    materiais: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    maestria: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # Masmorra em andamento {mapa, andar} (None fora de uma) e maior andar já vencido na Torre
    masmorra: Mapped[Optional[dict]] = mapped_column(JSON)
    torre_andar: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Arena: troféus (ranking), moedas gastas na loja da Arena e registro {v, d, seq, melhor, dia, vs: {nome: lutas hoje}}
    trofeus: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    moedas_arena: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    arena: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    torre_sessao: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # dentro da Torre: sem hospital nem descanso
    progresso: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)  # missões, conquistas, login e passe (progresso_extra.py)
    # Amigos, skins, sexo, retorno à organização e outros dados pessoais (blocos_extra.py); fama Nukenin (0 a 100.000) e organização criminosa
    blocos: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    nk_fama: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True)
    org_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    arena_temp: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # temporada em que os troféus valem

    # ── Shadow Clan, fase 1 (aditivo; ensure_columns cria estas colunas em bancos existentes) ──
    # Classe: taijutsu | ninjutsu | genjutsu | marionetista | ninja_medico (None = ainda não escolheu)
    classe: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    # Ouro Ninja (moeda forte): compra slots de pet, reset de classe, VIP e Passe
    ouro_ninja: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    # Slots de pet liberados: 2 iniciais, +1 por 100 Ouro Ninja, máximo 20 (limite validado no código)
    pet_slots: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    # 5 atributos universais (Bloco 2). Só preenchidos quando os pontos antigos forem convertidos.
    atr_str: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    atr_int: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    atr_agi: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    atr_dex: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    atr_vig: Mapped[int] = mapped_column(Integer, default=5, nullable=False)

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

    # Kage NPC enquanto nenhum jogador ocupa o cargo; desafiante e início da disputa (7 dias seguidos com mais fama)
    npc_kage: Mapped[Optional[str]] = mapped_column(String(64))
    npc_fama: Mapped[int] = mapped_column(Integer, default=20000, nullable=False)
    challenger_id: Mapped[Optional[int]] = mapped_column(Integer)
    challenger_since: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

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
    # Liderança: sem jogador líder, o líder NPC (clas_dados.py) comanda. Disputa de 7 dias por pontos de clã.
    lider_id: Mapped[Optional[int]] = mapped_column(Integer)
    desafiante_id: Mapped[Optional[int]] = mapped_column(Integer)
    desafio_desde: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reliquia_dada: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # relíquia única no mundo

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
    # Guilda completa: mural, vices [player_id], chefe da guilda {n, hp, hp_max, dano}, recompensas pendentes e tentativas por dia
    dados: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

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


# ─────────────────────────────────────────────────────────────
# Mercado entre jogadores: o item sai da mochila do vendedor (custódia) e volta se o anúncio for cancelado
# ─────────────────────────────────────────────────────────────
class MarketListing(Base):
    __tablename__ = "market_listings"

    id: Mapped[int] = mapped_column(primary_key=True)
    seller_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    item_name: Mapped[str] = mapped_column(String(64), nullable=False)
    item_type: Mapped[str] = mapped_column(String(16), nullable=False)
    bonus: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    preco: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# ─────────────────────────────────────────────────────────────
# Pets: nascem de ovos, têm uma única natureza (que define o atributo que dão ao dono) e afinidade de 0 a 5000
# ─────────────────────────────────────────────────────────────
class Pet(Base):
    __tablename__ = "pets"

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    especie: Mapped[str] = mapped_column(String(48), nullable=False)
    raridade: Mapped[int] = mapped_column(Integer, nullable=False)  # 0 comum, 1 incomum, 2 raro, 3 lendário
    natureza: Mapped[str] = mapped_column(String(24), nullable=False)
    afinidade: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ativo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


# ─────────────────────────────────────────────────────────────
# Chat: canal "global", "guilda:<id>" ou "dm:<menor>-<maior>" (ids dos jogadores). Mensagens antigas são apagadas sozinhas.
# ─────────────────────────────────────────────────────────────
class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    canal: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    autor_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    autor_nome: Mapped[str] = mapped_column(String(32), nullable=False)
    destino_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)  # só nas mensagens privadas
    texto: Mapped[str] = mapped_column(String(300), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Organizacao(Base):
    """Organização criminosa de Nukenin (fundada com 2.000 de fama Nukenin)."""
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    lider_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)  # sem FK: a liderança passa de mão em mão
    dados: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


# ─────────────────────────────────────────────────────────────
# Shadow Clan, fase 1 — tabelas novas (o create_all cria; nenhuma tabela existente é alterada ou apagada)
# ─────────────────────────────────────────────────────────────
class ClasseDef(Base):
    """Catálogo das 5 especializações (Bloco 3). Dados semeados por semear_classes()."""
    __tablename__ = "classes"

    codigo: Mapped[str] = mapped_column(String(16), primary_key=True)  # taijutsu, ninjutsu, ...
    nome: Mapped[str] = mapped_column(String(32), nullable=False)
    recurso: Mapped[str] = mapped_column(String(8), nullable=False)  # "chakra" ou "furia" (Taijutsu)
    atributo_principal: Mapped[str] = mapped_column(String(3), nullable=False)  # str|int|agi|dex|vig
    atributo_secundario: Mapped[str] = mapped_column(String(3), nullable=False)
    ignora_elemento: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # Taijutsu: modificador sempre 1.0
    dano_verdadeiro: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # Ninja Médico: ignora Vigor
    bonus_elemental: Mapped[float] = mapped_column(Float, default=1.25, nullable=False)  # Ninjutsu: 1.50
    equip_permitido: Mapped[list] = mapped_column(JSON, default=list, nullable=False)  # tipos de item liberados (Bloco 5)

    __table_args__ = (
        CheckConstraint("recurso IN ('chakra','furia')", name="ck_classe_recurso"),
        CheckConstraint("atributo_principal IN ('str','int','agi','dex','vig')", name="ck_classe_attr_p"),
        CheckConstraint("atributo_secundario IN ('str','int','agi','dex','vig')", name="ck_classe_attr_s"),
    )


class QuestInventory(Base):
    """Itens de entrega/coleta de missão (ex.: Carvão Brilhante). Oculta e NÃO ocupa espaço na mochila (Bloco 1)."""
    __tablename__ = "quest_inventory"

    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"), primary_key=True)
    item_id: Mapped[str] = mapped_column(String(48), primary_key=True)  # id do item no catálogo do jogo
    quantidade: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    __table_args__ = (CheckConstraint("quantidade > 0", name="ck_quest_inv_qtd"),)


class BossThreat(Base):
    """Pity do Boss: Pontos de Ameaça por jogador e por mapa (Bloco 4). +1 por Explorar sem Boss; 100 = Boss garantido."""
    __tablename__ = "boss_threat"

    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"), primary_key=True)
    mapa_id: Mapped[int] = mapped_column(Integer, primary_key=True)  # id do mapa (os mapas ficam em game_extra.py, sem tabela)
    pontos: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (CheckConstraint("pontos >= 0 AND pontos <= 100", name="ck_boss_threat_pontos"),)


def semear_classes(db) -> None:
    """Idempotente: insere as 5 classes que ainda não existem. Chame junto do seed_initial_data()."""
    from combate import CLASSES
    existentes = {c for (c,) in db.execute(ClasseDef.__table__.select().with_only_columns(ClasseDef.codigo))}
    for codigo, r in CLASSES.items():
        if codigo in existentes:
            continue
        db.add(ClasseDef(
            codigo=codigo, nome=r["nome"], recurso="furia" if codigo == "taijutsu" else "chakra",
            atributo_principal=r["principal"], atributo_secundario=r["secundario"],
            ignora_elemento=r["ignora_elemento"], dano_verdadeiro=r["dano_verdadeiro"],
            bonus_elemental=r["bonus_elemental"], equip_permitido=list(r["equip"]),
        ))
    db.commit()
