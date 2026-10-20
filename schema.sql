-- ============================================================================
-- SHADOW CLAN RPG — Schema SQL Completo e Unificado (PostgreSQL)
-- ============================================================================

BEGIN;

DROP TABLE IF EXISTS currency_ledger, player_cosmetics, player_pass_claims, player_pass,
    elite_pass_track, free_pass_track, season_passes, dungeon_runs, market_listings,
    player_marionetes, player_pets, quest_inventory, inventory_items, player_map_threat,
    chat_messages, chat_mensagens, organizations, boss_threat, player_summons, players, 
    items, maps, classes CASCADE;

-- CLASSES
CREATE TABLE classes (
    id                  SMALLINT PRIMARY KEY,
    codigo              TEXT NOT NULL UNIQUE,
    nome                TEXT NOT NULL,
    recurso             TEXT NOT NULL CHECK (recurso IN ('chakra', 'furia')),
    atributo_principal  TEXT NOT NULL CHECK (atributo_principal IN ('str','int','agi','dex','vig')),
    atributo_secundario TEXT NOT NULL CHECK (atributo_secundario IN ('str','int','agi','dex','vig')),
    ignora_elemento     BOOLEAN NOT NULL DEFAULT FALSE,
    dano_verdadeiro     BOOLEAN NOT NULL DEFAULT FALSE,
    bonus_elemental     NUMERIC(4,2) NOT NULL DEFAULT 1.25,
    equip_permitido     TEXT[] NOT NULL
);

INSERT INTO classes VALUES
 (1,'taijutsu',    'Taijutsu',     'furia',  'str','agi', TRUE,  FALSE, 1.00, ARRAY['manopla','armadura_pesada','couraca']),
 (2,'ninjutsu',    'Ninjutsu',     'chakra', 'int','dex', FALSE, FALSE, 1.50, ARRAY['katana','manto']),
 (3,'genjutsu',    'Genjutsu',     'chakra', 'int','agi', FALSE, FALSE, 1.25, ARRAY['leque','flauta','traje_couro']),
 (4,'marionetista','Marionetista', 'chakra', 'int','dex', FALSE, FALSE, 1.25, ARRAY['nucleo','componente_madeira']),
 (5,'ninja_medico','Ninja Médico', 'chakra', 'int','dex', FALSE, TRUE,  1.25, ARRAY['bisturi','tunica_alva']);

-- MAPAS
CREATE TABLE maps (
    id          SERIAL PRIMARY KEY,
    nome        TEXT NOT NULL,
    nivel_min   SMALLINT NOT NULL,
    nivel_max   SMALLINT NOT NULL,
    CHECK (nivel_min <= nivel_max)
);

-- CATÁLOGO DE ITENS
CREATE TABLE items (
    id              SERIAL PRIMARY KEY,
    nome            TEXT NOT NULL,
    categoria       TEXT NOT NULL CHECK (categoria IN ('equipamento','material','consumivel','fragmento_mapa','marionete','quest','cosmetico')),
    tipo_equip      TEXT,
    raridade        TEXT NOT NULL DEFAULT 'comum' CHECK (raridade IN ('comum','incomum','raro','lendario')),
    tier            SMALLINT NOT NULL DEFAULT 1,
    dano_arma       INTEGER NOT NULL DEFAULT 0,
    bonus_str       INTEGER NOT NULL DEFAULT 0, 
    bonus_int       INTEGER NOT NULL DEFAULT 0,
    bonus_agi       INTEGER NOT NULL DEFAULT 0, 
    bonus_dex       INTEGER NOT NULL DEFAULT 0,
    bonus_vig       INTEGER NOT NULL DEFAULT 0,
    marionete_hp_max INTEGER,
    preco_venda     INTEGER NOT NULL DEFAULT 0,
    intransferivel  BOOLEAN NOT NULL DEFAULT FALSE
);

-- JOGADORES
CREATE TABLE players (
    id              BIGSERIAL PRIMARY KEY,
    telegram_id     BIGINT NOT NULL UNIQUE,
    username        VARCHAR(64),
    ninja_name      VARCHAR(32) NOT NULL UNIQUE,
    classe          VARCHAR(16),
    rank            VARCHAR(24) NOT NULL DEFAULT 'Estudante',
    level           SMALLINT NOT NULL DEFAULT 1 CHECK (level >= 1),
    exp             BIGINT NOT NULL DEFAULT 0 CHECK (exp >= 0),
    ryos            BIGINT NOT NULL DEFAULT 500 CHECK (ryos >= 0),
    ouro_ninja      BIGINT NOT NULL DEFAULT 0 CHECK (ouro_ninja >= 0),
    energia         SMALLINT NOT NULL DEFAULT 25 CHECK (energia >= 0),
    reputation      INTEGER NOT NULL DEFAULT 0,
    journey         INTEGER NOT NULL DEFAULT 0,
    journey_prog    INTEGER NOT NULL DEFAULT 0,
    fama            BIGINT NOT NULL DEFAULT 0 CHECK (fama >= 0),
    nk_fama         INTEGER NOT NULL DEFAULT 0 CHECK (nk_fama >= 0),
    avatar          VARCHAR(8) NOT NULL DEFAULT '🥷',
    elemento        VARCHAR(8) NOT NULL DEFAULT '',
    elementos_extra JSONB NOT NULL DEFAULT '[]'::jsonb,
    jutsus          JSONB NOT NULL DEFAULT '{}'::jsonb,
    pocoes          JSONB NOT NULL DEFAULT '{}'::jsonb,
    alimentos       JSONB NOT NULL DEFAULT '{}'::jsonb,
    ovos            JSONB NOT NULL DEFAULT '{}'::jsonb,
    materiais       JSONB NOT NULL DEFAULT '{}'::jsonb,
    maestria        JSONB NOT NULL DEFAULT '{}'::jsonb,
    abates          JSONB NOT NULL DEFAULT '{}'::jsonb,
    status_fora     JSONB NOT NULL DEFAULT '[]'::jsonb,
    luta            JSONB,
    buff            JSONB,
    progresso       JSONB NOT NULL DEFAULT '{}'::jsonb,
    blocos          JSONB NOT NULL DEFAULT '{}'::jsonb,
    marionete       JSONB NOT NULL DEFAULT '{}'::jsonb,
    passiva_medica  VARCHAR(16) NOT NULL DEFAULT '',
    pet_slots       SMALLINT NOT NULL DEFAULT 2 CHECK (pet_slots BETWEEN 2 AND 20),
    vip_plano       VARCHAR(20),
    vip_expira_em   TIMESTAMPTZ,
    org_id          INTEGER,
    village_id      INTEGER NOT NULL,
    clan_id         INTEGER NOT NULL,
    guilda_id       INTEGER,
    regen_at        TIMESTAMPTZ,
    energia_at      TIMESTAMPTZ,
    hospital_gratis_at TIMESTAMPTZ,
    sannin_desde    TIMESTAMPTZ,
    journey_ends_at TIMESTAMPTZ,
    criado_em       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    atualizado_em   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- INVENTÁRIO
CREATE TABLE inventory_items (
    id              BIGSERIAL PRIMARY KEY,
    player_id       BIGINT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    item_id         INTEGER REFERENCES items(id),
    item_name       VARCHAR(64) NOT NULL,
    item_type       VARCHAR(16) NOT NULL,
    status_bonus    JSONB NOT NULL DEFAULT '{}'::jsonb,
    quantidade      INTEGER NOT NULL DEFAULT 1 CHECK (quantidade > 0),
    equipped        BOOLEAN NOT NULL DEFAULT FALSE,
    raridade_forjada VARCHAR(16),
    bonus_pct       SMALLINT NOT NULL DEFAULT 0,
    atributo_extra  TEXT,
    slot_passivo    TEXT
);

-- ASSOCIAÇÃO INVOCADOS
CREATE TABLE player_summons (
    player_id   BIGINT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    summon_id   INTEGER NOT NULL,
    level       INTEGER NOT NULL DEFAULT 1,
    is_active   BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (player_id, summon_id)
);

-- QUEST INVENTORY
CREATE TABLE quest_inventory (
    player_id   BIGINT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    item_id     VARCHAR(48) NOT NULL,
    quantidade  INTEGER NOT NULL CHECK (quantidade > 0),
    PRIMARY KEY (player_id, item_id)
);

-- PITY BOSS THREAT
CREATE TABLE boss_threat (
    player_id   BIGINT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    mapa_id     INTEGER NOT NULL,
    pontos      INTEGER NOT NULL DEFAULT 0 CHECK (pontos BETWEEN 0 AND 100),
    PRIMARY KEY (player_id, mapa_id)
);

-- MERCADO P2P
CREATE TABLE market_listings (
    id          BIGSERIAL PRIMARY KEY,
    seller_id   BIGINT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    item_name   VARCHAR(64) NOT NULL,
    item_type   VARCHAR(16) NOT NULL,
    bonus       JSONB NOT NULL DEFAULT '{}'::jsonb,
    preco       INTEGER NOT NULL CHECK (preco > 0),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- PETS
CREATE TABLE pets (
    id          BIGSERIAL PRIMARY KEY,
    player_id   BIGINT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    especie     VARCHAR(48) NOT NULL,
    raridade    INTEGER NOT NULL,
    natureza    VARCHAR(24) NOT NULL,
    afinidade   INTEGER NOT NULL DEFAULT 0,
    ativo       BOOLEAN NOT NULL DEFAULT FALSE
);

-- CHAT MESSAGES
CREATE TABLE chat_messages (
    id          BIGSERIAL PRIMARY KEY,
    canal       VARCHAR(48) NOT NULL,
    autor_id    BIGINT NOT NULL,
    autor_nome  VARCHAR(32) NOT NULL,
    destino_id  BIGINT,
    texto       VARCHAR(300) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ORGANIZAÇÃO NUKENIN
CREATE TABLE organizations (
    id          BIGSERIAL PRIMARY KEY,
    nome        VARCHAR(40) NOT NULL UNIQUE,
    lider_id    BIGINT NOT NULL,
    dados       JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- GUILDAS
CREATE TABLE guilds (
    id              BIGSERIAL PRIMARY KEY,
    name            VARCHAR(64) NOT NULL UNIQUE,
    leader_id       BIGINT NOT NULL,
    level           INTEGER NOT NULL DEFAULT 1,
    recursos_doados BIGINT NOT NULL DEFAULT 0,
    vitorias_guerra INTEGER NOT NULL DEFAULT 0,
    dados           JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- AUDITORIA RAZÃO
CREATE TABLE currency_ledger (
    id          BIGSERIAL PRIMARY KEY,
    player_id   BIGINT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    moeda       TEXT NOT NULL CHECK (moeda IN ('ryos','ouro_ninja')),
    valor       BIGINT NOT NULL,
    motivo      TEXT NOT NULL,
    criado_em   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMIT;
