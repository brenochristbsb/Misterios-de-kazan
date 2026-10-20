-- ============================================================================
-- SHADOW CLAN RPG — Schema SQL (PostgreSQL)
-- Segue os Blocos 1 a 6 do win.txt. Banco novo (sem jogadores): pode recriar do zero.
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- LIMPEZA (recria tudo do zero)
-- ----------------------------------------------------------------------------
DROP TABLE IF EXISTS currency_ledger, player_cosmetics, player_pass_claims, player_pass,
    elite_pass_track, free_pass_track, season_passes, dungeon_runs, market_listings,
    player_marionetes, player_pets, quest_inventory, inventory_items, player_map_threat,
    players, items, maps, classes CASCADE;

-- ----------------------------------------------------------------------------
-- BLOCO 3: CLASSES (5 especializações)
-- ----------------------------------------------------------------------------
CREATE TABLE classes (
    id              SMALLINT PRIMARY KEY,
    codigo          TEXT NOT NULL UNIQUE,
    nome            TEXT NOT NULL,
    recurso         TEXT NOT NULL CHECK (recurso IN ('chakra', 'furia')),
    atributo_principal  TEXT NOT NULL CHECK (atributo_principal IN ('str','int','agi','dex','vig')),
    atributo_secundario TEXT NOT NULL CHECK (atributo_secundario IN ('str','int','agi','dex','vig')),
    ignora_elemento BOOLEAN NOT NULL DEFAULT FALSE,   -- Taijutsu: modificador sempre 1.0
    dano_verdadeiro BOOLEAN NOT NULL DEFAULT FALSE,   -- Ninja Médico: ignora Vigor
    bonus_elemental NUMERIC(4,2) NOT NULL DEFAULT 1.25, -- Ninjutsu: 1.50
    equip_permitido TEXT[] NOT NULL                    -- tipos de item liberados (Bloco 5)
);

INSERT INTO classes VALUES
 (1,'taijutsu',    'Taijutsu',     'furia',  'str','agi', TRUE,  FALSE, 1.25, ARRAY['manopla','armadura_pesada']),
 (2,'ninjutsu',    'Ninjutsu',     'chakra', 'int','dex', FALSE, FALSE, 1.50, ARRAY['katana','manto']),
 (3,'genjutsu',    'Genjutsu',     'chakra', 'int','agi', FALSE, FALSE, 1.25, ARRAY['leque','flauta','traje_couro']),
 (4,'marionetista','Marionetista', 'chakra', 'int','dex', FALSE, FALSE, 1.25, ARRAY['nucleo','componente_madeira']),
 (5,'ninja_medico','Ninja Médico', 'chakra', 'int','dex', FALSE, TRUE,  1.25, ARRAY['bisturi','tunica_alva']);

-- ----------------------------------------------------------------------------
-- MAPAS
-- ----------------------------------------------------------------------------
CREATE TABLE maps (
    id          SERIAL PRIMARY KEY,
    nome        TEXT NOT NULL,
    nivel_min   SMALLINT NOT NULL,
    nivel_max   SMALLINT NOT NULL,
    CHECK (nivel_min <= nivel_max)
);

-- ----------------------------------------------------------------------------
-- CATÁLOGO DE ITENS
-- ----------------------------------------------------------------------------
CREATE TABLE items (
    id              SERIAL PRIMARY KEY,
    nome            TEXT NOT NULL,
    categoria       TEXT NOT NULL CHECK (categoria IN
                      ('equipamento','material','consumivel','fragmento_mapa','marionete','quest','cosmetico')),
    tipo_equip      TEXT,                              -- manopla, katana, bisturi... (trava por classe)
    raridade        TEXT NOT NULL DEFAULT 'comum'
                      CHECK (raridade IN ('comum','incomum','raro','lendario')),
    tier            SMALLINT NOT NULL DEFAULT 1,
    dano_arma       INTEGER NOT NULL DEFAULT 0,
    bonus_str SMALLINT NOT NULL DEFAULT 0, bonus_int SMALLINT NOT NULL DEFAULT 0,
    bonus_agi SMALLINT NOT NULL DEFAULT 0, bonus_dex SMALLINT NOT NULL DEFAULT 0,
    bonus_vig SMALLINT NOT NULL DEFAULT 0,
    marionete_hp_max INTEGER,                          -- só para categoria 'marionete'
    preco_venda     INTEGER NOT NULL DEFAULT 0,
    intransferivel  BOOLEAN NOT NULL DEFAULT FALSE,    -- cosméticos sazonais do Passe
    -- itens de quest nunca entram na mochila (Bloco 1)
    CHECK (categoria <> 'equipamento' OR tipo_equip IS NOT NULL)
);

-- ----------------------------------------------------------------------------
-- BLOCO 1 + 2 + 6: JOGADORES
-- ----------------------------------------------------------------------------
CREATE TABLE players (
    id              BIGSERIAL PRIMARY KEY,
    telegram_id     BIGINT NOT NULL UNIQUE,
    nome            TEXT NOT NULL UNIQUE,
    classe_id       SMALLINT REFERENCES classes(id),
    nivel           SMALLINT NOT NULL DEFAULT 1 CHECK (nivel >= 1),
    xp              BIGINT NOT NULL DEFAULT 0 CHECK (xp >= 0),

    -- moedas
    ryos            BIGINT NOT NULL DEFAULT 0 CHECK (ryos >= 0),
    ouro_ninja      BIGINT NOT NULL DEFAULT 0 CHECK (ouro_ninja >= 0),   -- Hard Currency
    energia         SMALLINT NOT NULL DEFAULT 0 CHECK (energia >= 0),

    -- BLOCO 2: 5 atributos universais
    str             INTEGER NOT NULL DEFAULT 5 CHECK (str >= 0),
    "int"           INTEGER NOT NULL DEFAULT 5 CHECK ("int" >= 0),
    agi             INTEGER NOT NULL DEFAULT 5 CHECK (agi >= 0),
    dex             INTEGER NOT NULL DEFAULT 5 CHECK (dex >= 0),
    vig             INTEGER NOT NULL DEFAULT 5 CHECK (vig >= 0),
    pontos_livres   INTEGER NOT NULL DEFAULT 0 CHECK (pontos_livres >= 0),

    -- recursos em combate (chakra OU fúria, conforme a classe)
    hp_atual        INTEGER NOT NULL DEFAULT 100 CHECK (hp_atual >= 0),
    recurso_atual   INTEGER NOT NULL DEFAULT 0   CHECK (recurso_atual >= 0),

    -- BLOCO 1: slots de pets (2 iniciais, +1 por vez, máximo 20)
    pet_slots       SMALLINT NOT NULL DEFAULT 2 CHECK (pet_slots BETWEEN 2 AND 20),

    -- BLOCO 6: VIP
    vip_tier        TEXT NOT NULL DEFAULT 'nenhum' CHECK (vip_tier IN ('nenhum','shinobi','shogun')),
    vip_expira_em   TIMESTAMPTZ,
    moldura_perfil  TEXT NOT NULL DEFAULT 'padrao',

    hospital_ate    TIMESTAMPTZ,
    criado_em       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    atualizado_em   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ----------------------------------------------------------------------------
-- BLOCO 1: INVENTÁRIO PRIMÁRIO (mochila) — capacidade 30 / 40 / 60 conforme VIP
-- ----------------------------------------------------------------------------
CREATE TABLE inventory_items (
    id              BIGSERIAL PRIMARY KEY,
    player_id       BIGINT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    item_id         INTEGER NOT NULL REFERENCES items(id),
    quantidade      INTEGER NOT NULL DEFAULT 1 CHECK (quantidade > 0),
    equipped        BOOLEAN NOT NULL DEFAULT FALSE,

    -- BLOCO 5: resultado da Forja
    raridade_forjada TEXT CHECK (raridade_forjada IN ('comum','incomum','raro','lendario')),
    bonus_pct        SMALLINT NOT NULL DEFAULT 0,        -- 0 / 5 / 10 / 20
    atributo_extra   TEXT CHECK (atributo_extra IN ('str','int','agi','dex','vig')),
    slot_passivo     TEXT                                -- exclusivo da classe (Lendário)
);
CREATE INDEX idx_inv_player ON inventory_items(player_id);
-- equipado = item único (não empilha)
ALTER TABLE inventory_items ADD CONSTRAINT ck_equipado_unico CHECK (NOT equipped OR quantidade = 1);

-- ----------------------------------------------------------------------------
-- BLOCO 1: QUEST_INVENTORY (oculto, não ocupa espaço na mochila)
-- ----------------------------------------------------------------------------
CREATE TABLE quest_inventory (
    player_id   BIGINT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    item_id     INTEGER NOT NULL REFERENCES items(id),
    quantidade  INTEGER NOT NULL CHECK (quantidade > 0),
    PRIMARY KEY (player_id, item_id)
);

-- ----------------------------------------------------------------------------
-- BLOCO 1: PETS
-- ----------------------------------------------------------------------------
CREATE TABLE player_pets (
    id          BIGSERIAL PRIMARY KEY,
    player_id   BIGINT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    pet_item_id INTEGER NOT NULL REFERENCES items(id),
    nome        TEXT,
    adquirido_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_pets_player ON player_pets(player_id);

-- ----------------------------------------------------------------------------
-- BLOCO 3: MARIONETE EM BATALHA (HP próprio)
-- ----------------------------------------------------------------------------
CREATE TABLE player_marionetes (
    inventory_item_id BIGINT PRIMARY KEY REFERENCES inventory_items(id) ON DELETE CASCADE,
    hp_atual    INTEGER NOT NULL CHECK (hp_atual >= 0),
    hp_max      INTEGER NOT NULL CHECK (hp_max > 0),
    CHECK (hp_atual <= hp_max)
);

-- ----------------------------------------------------------------------------
-- BLOCO 4: PITY DE BOSS (Pontos de Ameaça por jogador e por mapa)
-- ----------------------------------------------------------------------------
CREATE TABLE player_map_threat (
    player_id       BIGINT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    map_id          INTEGER NOT NULL REFERENCES maps(id),
    pontos_ameaca   SMALLINT NOT NULL DEFAULT 0 CHECK (pontos_ameaca BETWEEN 0 AND 100),
    PRIMARY KEY (player_id, map_id)
);

-- ----------------------------------------------------------------------------
-- BLOCO 5: MERCADO P2P (taxa de 10 Ryos ao listar + 5% na venda)
-- ----------------------------------------------------------------------------
CREATE TABLE market_listings (
    id                  BIGSERIAL PRIMARY KEY,
    seller_id           BIGINT NOT NULL REFERENCES players(id),
    inventory_item_id   BIGINT NOT NULL UNIQUE REFERENCES inventory_items(id),
    preco               BIGINT NOT NULL CHECK (preco > 0),
    taxa_anuncio        INTEGER NOT NULL DEFAULT 10 CHECK (taxa_anuncio >= 0), -- Shogun = 0
    status              TEXT NOT NULL DEFAULT 'ativo' CHECK (status IN ('ativo','vendido','cancelado')),
    buyer_id            BIGINT REFERENCES players(id),
    taxa_venda          BIGINT,                                                -- 5% retido
    criado_em           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    vendido_em          TIMESTAMPTZ
);
CREATE INDEX idx_market_status ON market_listings(status);

-- BLOCO 1: item equipado não pode ser anunciado
CREATE OR REPLACE FUNCTION fn_bloqueia_anuncio_equipado() RETURNS TRIGGER AS $$
BEGIN
    IF (SELECT equipped FROM inventory_items WHERE id = NEW.inventory_item_id) THEN
        RAISE EXCEPTION 'Desequipe o item antes de anunciá-lo no mercado';
    END IF;
    RETURN NEW;
END; $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_anuncio_equipado
BEFORE INSERT ON market_listings
FOR EACH ROW EXECUTE FUNCTION fn_bloqueia_anuncio_equipado();

-- ----------------------------------------------------------------------------
-- BLOCO 5: MASMORRAS (fuga/morte = expulsão + perda dos 3 fragmentos)
-- ----------------------------------------------------------------------------
CREATE TABLE dungeon_runs (
    id                      BIGSERIAL PRIMARY KEY,
    player_id               BIGINT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    map_id                  INTEGER REFERENCES maps(id),
    andar_atual             SMALLINT NOT NULL DEFAULT 1,
    fragmentos_consumidos   SMALLINT NOT NULL DEFAULT 3,
    estado                  TEXT NOT NULL DEFAULT 'em_andamento'
                              CHECK (estado IN ('em_andamento','concluida','fugiu','morreu')),
    iniciada_em             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    encerrada_em            TIMESTAMPTZ
);
-- só uma masmorra ativa por jogador
CREATE UNIQUE INDEX uq_dungeon_ativa ON dungeon_runs(player_id) WHERE estado = 'em_andamento';

-- ----------------------------------------------------------------------------
-- BLOCO 6: PASSE DE TEMPORADA (28 dias) — Free e Elite
-- ----------------------------------------------------------------------------
CREATE TABLE season_passes (
    id          SERIAL PRIMARY KEY,
    nome        TEXT NOT NULL,
    inicio      TIMESTAMPTZ NOT NULL,
    fim         TIMESTAMPTZ NOT NULL,
    preco_elite INTEGER NOT NULL DEFAULT 250,          -- Ouro Ninja
    CHECK (fim > inicio)
);

CREATE TABLE free_pass_track (
    season_id       INTEGER NOT NULL REFERENCES season_passes(id) ON DELETE CASCADE,
    nivel           SMALLINT NOT NULL CHECK (nivel >= 1),
    xp_necessario   INTEGER NOT NULL CHECK (xp_necessario >= 0),
    recompensa_tipo TEXT NOT NULL CHECK (recompensa_tipo IN ('ryos','ouro_ninja','item','energia')),
    item_id         INTEGER REFERENCES items(id),
    quantidade      INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (season_id, nivel)
);

CREATE TABLE elite_pass_track (
    season_id       INTEGER NOT NULL REFERENCES season_passes(id) ON DELETE CASCADE,
    nivel           SMALLINT NOT NULL CHECK (nivel >= 1),
    xp_necessario   INTEGER NOT NULL CHECK (xp_necessario >= 0),
    recompensa_tipo TEXT NOT NULL CHECK (recompensa_tipo IN ('ryos','ouro_ninja','item','energia','cosmetico')),
    item_id         INTEGER REFERENCES items(id),
    quantidade      INTEGER NOT NULL DEFAULT 1,
    exclusivo_sazonal BOOLEAN NOT NULL DEFAULT FALSE,   -- níveis 1, 15 e 30: fundo, título, skin/aura
    PRIMARY KEY (season_id, nivel)
);

CREATE TABLE player_pass (
    player_id       BIGINT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    season_id       INTEGER NOT NULL REFERENCES season_passes(id),
    xp_passe        BIGINT NOT NULL DEFAULT 0 CHECK (xp_passe >= 0),
    nivel           SMALLINT NOT NULL DEFAULT 0,
    elite_ativo     BOOLEAN NOT NULL DEFAULT FALSE,     -- +10% de XP do Passe em missões diárias/semanais
    elite_comprado_em TIMESTAMPTZ,
    PRIMARY KEY (player_id, season_id)
);

CREATE TABLE player_pass_claims (
    player_id   BIGINT NOT NULL,
    season_id   INTEGER NOT NULL,
    trilha      TEXT NOT NULL CHECK (trilha IN ('free','elite')),
    nivel       SMALLINT NOT NULL,
    resgatado_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (player_id, season_id, trilha, nivel),
    FOREIGN KEY (player_id, season_id) REFERENCES player_pass(player_id, season_id) ON DELETE CASCADE
);

-- Cosméticos intransferíveis ligados à conta
CREATE TABLE player_cosmetics (
    player_id   BIGINT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    item_id     INTEGER NOT NULL REFERENCES items(id),
    season_id   INTEGER REFERENCES season_passes(id),
    obtido_em   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (player_id, item_id)
);

-- ----------------------------------------------------------------------------
-- SEGURANÇA: livro-razão de Ouro Ninja / Ryos (auditoria de compras, resets, VIP)
-- ----------------------------------------------------------------------------
CREATE TABLE currency_ledger (
    id          BIGSERIAL PRIMARY KEY,
    player_id   BIGINT NOT NULL REFERENCES players(id),
    moeda       TEXT NOT NULL CHECK (moeda IN ('ryos','ouro_ninja')),
    valor       BIGINT NOT NULL,                        -- negativo = gasto
    motivo      TEXT NOT NULL,                          -- 'pet_slot','reset_classe','vip_shogun','taxa_p2p'...
    criado_em   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_ledger_player ON currency_ledger(player_id, criado_em DESC);

COMMIT;
