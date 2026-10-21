# Gerado a partir de schema.sql (copia embutida: nao depende de enviar o arquivo .sql)
SCHEMA = r"""-- Shadow Clan RPG · schema v1 (GDD v1.2, seções 6, 8, 11, 14, 18-29)
-- REGRA DE OURO: o banco dos jogadores NUNCA é apagado. Mudanças futuras entram como
-- migrações aditivas (migrations/NNN_nome.sql), registradas em schema_migrations.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    versao      INTEGER PRIMARY KEY,
    nome        TEXT NOT NULL,
    aplicada_em TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ------------------------------------------------------------ mundo
CREATE TABLE IF NOT EXISTS villages (
    id            INTEGER PRIMARY KEY,
    nome          TEXT NOT NULL UNIQUE,
    significado   TEXT NOT NULL,
    pedra_selo    TEXT NOT NULL,
    cofre_ryos    INTEGER NOT NULL DEFAULT 0 CHECK (cofre_ryos >= 0),
    imposto_pct   REAL NOT NULL DEFAULT 0 CHECK (imposto_pct BETWEEN 0 AND 5),
    moral         REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS clans (
    id          INTEGER PRIMARY KEY,
    village_id  INTEGER NOT NULL REFERENCES villages(id),
    nome        TEXT NOT NULL UNIQUE,
    especialidade TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS zones (
    id          INTEGER PRIMARY KEY,
    nome        TEXT NOT NULL UNIQUE,
    dono_village_id INTEGER REFERENCES villages(id),
    neutra      INTEGER NOT NULL DEFAULT 0 CHECK (neutra IN (0, 1))
);

-- ---------------------------------------------------------- jogadores
CREATE TABLE IF NOT EXISTS players (
    id            INTEGER PRIMARY KEY,
    telegram_id   INTEGER NOT NULL UNIQUE,
    nome          TEXT NOT NULL UNIQUE COLLATE NOCASE CHECK (length(nome) BETWEEN 3 AND 20),
    sexo          TEXT NOT NULL CHECK (sexo IN ('M', 'F', 'O')),
    classe        TEXT NOT NULL CHECK (classe IN ('Taijutsu','Ninjutsu','Genjutsu','Marionetista','Kunoichi')),
    elemento      TEXT CHECK (elemento IN ('Fogo','Vento','Relâmpago','Terra','Água')),
    village_id    INTEGER REFERENCES villages(id),
    clan_id       INTEGER REFERENCES clans(id),
    nivel         INTEGER NOT NULL DEFAULT 1 CHECK (nivel BETWEEN 1 AND 100),
    xp            INTEGER NOT NULL DEFAULT 0 CHECK (xp >= 0),
    patente       TEXT NOT NULL DEFAULT 'Estudante'
                  CHECK (patente IN ('Estudante','Genin','Chunin','Jounin','Jounin Elite')),
    fama          INTEGER NOT NULL DEFAULT 0 CHECK (fama >= 0),
    pp_carreira   INTEGER NOT NULL DEFAULT 0 CHECK (pp_carreira >= 0),
    ryos          INTEGER NOT NULL DEFAULT 0 CHECK (ryos >= 0),
    ouro_ninja    INTEGER NOT NULL DEFAULT 0 CHECK (ouro_ninja >= 0),
    selos_arena   INTEGER NOT NULL DEFAULT 0 CHECK (selos_arena >= 0),
    selos_torre   INTEGER NOT NULL DEFAULT 0 CHECK (selos_torre >= 0),
    energia       INTEGER NOT NULL DEFAULT 25 CHECK (energia BETWEEN 0 AND 25),
    energia_em    TEXT NOT NULL DEFAULT (datetime('now')),  -- base da regeneração lazy (1 a cada 6 min)
    str INTEGER NOT NULL DEFAULT 5 CHECK (str >= 0),
    agi INTEGER NOT NULL DEFAULT 5 CHECK (agi >= 0),
    int INTEGER NOT NULL DEFAULT 5 CHECK (int >= 0),
    dex INTEGER NOT NULL DEFAULT 5 CHECK (dex >= 0),
    vig INTEGER NOT NULL DEFAULT 5 CHECK (vig >= 0),
    pontos_livres INTEGER NOT NULL DEFAULT 0 CHECK (pontos_livres >= 0),
    mochila_extra INTEGER NOT NULL DEFAULT 0 CHECK (mochila_extra BETWEEN 0 AND 10),  -- só Ouro Ninja, teto 40
    hp_atual      REAL,                       -- NULL = vida cheia (o Hospital restaura)
    buff_ataque_ate TEXT,                     -- tratamento Premium: +20% de ataque por 15 min
    hospital_livre_em TEXT,
    ascensoes     INTEGER NOT NULL DEFAULT 0 CHECK (ascensoes >= 0),
    titulo_equipado TEXT,
    desertou_em   TEXT,
    ultimo_acesso TEXT NOT NULL DEFAULT (datetime('now')),
    criado_em     TEXT NOT NULL DEFAULT (datetime('now'))
);
-- Taijutsu não tem elemento (GDD 2.1); as demais classes precisam de um.
CREATE TRIGGER IF NOT EXISTS trg_players_elemento_ins BEFORE INSERT ON players
WHEN (NEW.classe = 'Taijutsu' AND NEW.elemento IS NOT NULL) OR (NEW.classe <> 'Taijutsu' AND NEW.elemento IS NULL)
BEGIN SELECT RAISE(ABORT, 'elemento invalido para a classe'); END;

-- ------------------------------------------------------------- economia
-- Todo ganho/gasto passa por aqui. idem_key único = transação idempotente.
CREATE TABLE IF NOT EXISTS currency_ledger (
    id          INTEGER PRIMARY KEY,
    player_id   INTEGER NOT NULL REFERENCES players(id),
    moeda       TEXT NOT NULL CHECK (moeda IN ('ryos','ouro_ninja','selos_arena','selos_torre')),
    delta       INTEGER NOT NULL CHECK (delta <> 0),
    saldo_apos  INTEGER NOT NULL CHECK (saldo_apos >= 0),
    motivo      TEXT NOT NULL,
    idem_key    TEXT NOT NULL UNIQUE,
    criado_em   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_ledger_player ON currency_ledger(player_id, criado_em);

-- ------------------------------------------------------------- itens
CREATE TABLE IF NOT EXISTS items (            -- catálogo
    id          INTEGER PRIMARY KEY,
    chave       TEXT NOT NULL UNIQUE,
    nome        TEXT NOT NULL,
    slot        TEXT CHECK (slot IN ('cabeca','amuleto','peitoral','pernas','luvas','anel_dir','anel_esq',
                                     'arma','pergaminho','pet') OR slot IS NULL),   -- NULL = material/consumível
    raridade    TEXT NOT NULL DEFAULT 'comum' CHECK (raridade IN ('comum','incomum','raro','epico','lendario')),
    classe_restrita TEXT CHECK (classe_restrita IN ('Taijutsu','Ninjutsu','Genjutsu','Marionetista','Kunoichi')),
    dano        INTEGER NOT NULL DEFAULT 0,
    bonus_json  TEXT NOT NULL DEFAULT '{}',
    preco_npc   INTEGER NOT NULL DEFAULT 0,
    empilhavel  INTEGER NOT NULL DEFAULT 0 CHECK (empilhavel IN (0, 1))
);

CREATE TABLE IF NOT EXISTS inventory (        -- instâncias de item de um jogador
    id          INTEGER PRIMARY KEY,
    player_id   INTEGER NOT NULL REFERENCES players(id),
    item_id     INTEGER NOT NULL REFERENCES items(id),
    qualidade   TEXT NOT NULL DEFAULT 'Comum' CHECK (qualidade IN ('Comum','Refinado','Obra-Prima','Perfeito')),
    vinculado   INTEGER NOT NULL DEFAULT 0 CHECK (vinculado IN (0, 1)),   -- 1 = não negociável
    local       TEXT NOT NULL DEFAULT 'mochila' CHECK (local IN ('mochila','equipado')),
    slot        TEXT,
    quantidade  INTEGER NOT NULL DEFAULT 1 CHECK (quantidade >= 1),
    criado_em   TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK ((local = 'equipado' AND slot IS NOT NULL) OR (local = 'mochila' AND slot IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_equip_slot ON inventory(player_id, slot) WHERE local = 'equipado';
CREATE INDEX IF NOT EXISTS ix_inv_player ON inventory(player_id, local);

-- Mochila: 30 espaços (+ até 10 por Ouro Ninja). Itens de missão ficam em quest_inventory.
CREATE TRIGGER IF NOT EXISTS trg_mochila_cheia_ins BEFORE INSERT ON inventory
WHEN NEW.local = 'mochila' AND
     (SELECT count(*) FROM inventory WHERE player_id = NEW.player_id AND local = 'mochila')
     >= 30 + (SELECT mochila_extra FROM players WHERE id = NEW.player_id)
BEGIN SELECT RAISE(ABORT, 'mochila cheia'); END;
CREATE TRIGGER IF NOT EXISTS trg_mochila_cheia_upd BEFORE UPDATE OF local ON inventory
WHEN NEW.local = 'mochila' AND OLD.local = 'equipado' AND
     (SELECT count(*) FROM inventory WHERE player_id = NEW.player_id AND local = 'mochila')
     >= 30 + (SELECT mochila_extra FROM players WHERE id = NEW.player_id)
BEGIN SELECT RAISE(ABORT, 'mochila cheia'); END;

CREATE TABLE IF NOT EXISTS quest_inventory (
    player_id   INTEGER NOT NULL REFERENCES players(id),
    chave       TEXT NOT NULL,
    quantidade  INTEGER NOT NULL DEFAULT 1 CHECK (quantidade >= 0),
    PRIMARY KEY (player_id, chave)
);

CREATE TABLE IF NOT EXISTS materials (        -- materiais empilháveis (não ocupam espaço de equipamento)
    player_id   INTEGER NOT NULL REFERENCES players(id),
    chave       TEXT NOT NULL,
    quantidade  INTEGER NOT NULL DEFAULT 0 CHECK (quantidade >= 0),
    PRIMARY KEY (player_id, chave)
);

-- ---------------------------------------------------------- pets e marionete
CREATE TABLE IF NOT EXISTS pets (
    id          INTEGER PRIMARY KEY,
    player_id   INTEGER NOT NULL REFERENCES players(id),
    especie     TEXT NOT NULL,
    nome        TEXT NOT NULL,
    faro        INTEGER NOT NULL DEFAULT 1,
    coragem     INTEGER NOT NULL DEFAULT 1,
    agilidade   INTEGER NOT NULL DEFAULT 1,
    descansa_ate TEXT,
    ativo       INTEGER NOT NULL DEFAULT 0 CHECK (ativo IN (0, 1))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_pet_ativo ON pets(player_id) WHERE ativo = 1;

CREATE TABLE IF NOT EXISTS pet_expeditions (
    id          INTEGER PRIMARY KEY,
    pet_id      INTEGER NOT NULL REFERENCES pets(id),
    player_id   INTEGER NOT NULL REFERENCES players(id),
    duracao_min INTEGER NOT NULL CHECK (duracao_min IN (30, 120, 480, 1440)),
    inicio      TEXT NOT NULL DEFAULT (datetime('now')),
    termina     TEXT NOT NULL,
    coletada    INTEGER NOT NULL DEFAULT 0 CHECK (coletada IN (0, 1))
);

CREATE TABLE IF NOT EXISTS marionettes (
    player_id   INTEGER PRIMARY KEY REFERENCES players(id),
    hp          REAL NOT NULL CHECK (hp >= 0),
    hp_max      REAL NOT NULL CHECK (hp_max > 0),
    nivel       INTEGER NOT NULL DEFAULT 1
);

-- ------------------------------------------------------- exploração e torre
CREATE TABLE IF NOT EXISTS map_threat (       -- pity do Boss (GDD 8.4)
    player_id   INTEGER NOT NULL REFERENCES players(id),
    mapa        INTEGER NOT NULL CHECK (mapa BETWEEN 1 AND 10),
    pontos      INTEGER NOT NULL DEFAULT 0 CHECK (pontos BETWEEN 0 AND 100),
    PRIMARY KEY (player_id, mapa)
);

CREATE TABLE IF NOT EXISTS tower_progress (
    player_id   INTEGER PRIMARY KEY REFERENCES players(id),
    andar_max   INTEGER NOT NULL DEFAULT 0 CHECK (andar_max >= 0),
    checkpoint  INTEGER NOT NULL DEFAULT 0 CHECK (checkpoint >= 0)
);

CREATE TABLE IF NOT EXISTS dungeon_runs (
    id          INTEGER PRIMARY KEY,
    player_id   INTEGER NOT NULL REFERENCES players(id),
    estado      TEXT NOT NULL DEFAULT 'ativa' CHECK (estado IN ('ativa','concluida','falhou')),
    criado_em   TEXT NOT NULL DEFAULT (datetime('now')),
    finalizado_em TEXT
);

-- ---------------------------------------------------------------- mercado P2P
CREATE TABLE IF NOT EXISTS market_listings (
    id          INTEGER PRIMARY KEY,
    seller_id   INTEGER NOT NULL REFERENCES players(id),
    inventory_id INTEGER NOT NULL REFERENCES inventory(id),
    preco       INTEGER NOT NULL CHECK (preco > 0),
    taxa_listagem INTEGER NOT NULL DEFAULT 10,
    status      TEXT NOT NULL DEFAULT 'ativo' CHECK (status IN ('ativo','vendido','cancelado')),
    comprador_id INTEGER REFERENCES players(id),
    criado_em   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_listing_ativo ON market_listings(inventory_id) WHERE status = 'ativo';
-- Item equipado ou vinculado NÃO pode ser anunciado (GDD 6 e 22.1).
CREATE TRIGGER IF NOT EXISTS trg_listing_valida BEFORE INSERT ON market_listings
WHEN (SELECT local FROM inventory WHERE id = NEW.inventory_id) <> 'mochila'
  OR (SELECT vinculado FROM inventory WHERE id = NEW.inventory_id) = 1
  OR (SELECT player_id FROM inventory WHERE id = NEW.inventory_id) <> NEW.seller_id
BEGIN SELECT RAISE(ABORT, 'item nao pode ser anunciado'); END;
-- Item anunciado não pode ser equipado.
CREATE TRIGGER IF NOT EXISTS trg_equipar_anunciado BEFORE UPDATE OF local ON inventory
WHEN NEW.local = 'equipado' AND EXISTS
     (SELECT 1 FROM market_listings WHERE inventory_id = NEW.id AND status = 'ativo')
BEGIN SELECT RAISE(ABORT, 'item anunciado nao pode ser equipado'); END;

-- --------------------------------------------------------------- guilda (máx. 5)
CREATE TABLE IF NOT EXISTS guilds (
    id          INTEGER PRIMARY KEY,
    nome        TEXT NOT NULL UNIQUE,
    lider_id    INTEGER NOT NULL REFERENCES players(id),
    criada_em   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS guild_members (
    guild_id    INTEGER NOT NULL REFERENCES guilds(id),
    player_id   INTEGER NOT NULL UNIQUE REFERENCES players(id),
    entrou_em   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (guild_id, player_id)
);
CREATE TRIGGER IF NOT EXISTS trg_guilda_cheia BEFORE INSERT ON guild_members
WHEN (SELECT count(*) FROM guild_members WHERE guild_id = NEW.guild_id) >= 5
BEGIN SELECT RAISE(ABORT, 'guilda cheia (maximo 5)'); END;

-- ------------------------------------------------------------------ arena
CREATE TABLE IF NOT EXISTS arena_seasons (
    id          INTEGER PRIMARY KEY,
    inicio      TEXT NOT NULL,
    fim         TEXT NOT NULL            -- temporadas de 28 dias
);
CREATE TABLE IF NOT EXISTS arena_ratings (
    player_id   INTEGER NOT NULL REFERENCES players(id),
    season_id   INTEGER NOT NULL REFERENCES arena_seasons(id),
    mmr         INTEGER NOT NULL DEFAULT 1000,
    lutas_hoje  INTEGER NOT NULL DEFAULT 0 CHECK (lutas_hoje BETWEEN 0 AND 12),
    dia         TEXT,
    PRIMARY KEY (player_id, season_id)
);

-- ------------------------------------------------------- combate (logs e ativos)
CREATE TABLE IF NOT EXISTS battle_logs (
    id          INTEGER PRIMARY KEY,
    tipo        TEXT NOT NULL CHECK (tipo IN ('treino','pve','boss','arena','torre','masmorra','guerra','trono')),
    player_id   INTEGER NOT NULL REFERENCES players(id),
    rival       TEXT NOT NULL,
    ambiente    TEXT NOT NULL,
    vencedor    TEXT,
    rodadas     INTEGER NOT NULL,
    log_json    TEXT NOT NULL,
    cronica     TEXT,
    criado_em   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS active_battles (
    id          TEXT PRIMARY KEY,
    player_id   INTEGER NOT NULL UNIQUE REFERENCES players(id),   -- uma luta ativa por jogador
    tipo        TEXT NOT NULL,
    estado      BLOB NOT NULL,                                     -- estado serializado pelo servidor
    contexto_json TEXT NOT NULL DEFAULT '{}',
    criado_em   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ------------------------------------------------- jutsus, builds, playbooks
CREATE TABLE IF NOT EXISTS skill_nodes (
    id          INTEGER PRIMARY KEY,
    classe      TEXT NOT NULL,
    ramo        TEXT NOT NULL,
    chave       TEXT NOT NULL UNIQUE,
    nome        TEXT NOT NULL,
    tags        TEXT NOT NULL DEFAULT '',
    efeito_json TEXT NOT NULL DEFAULT '{}',
    custo       INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS player_skills (
    player_id   INTEGER NOT NULL REFERENCES players(id),
    node_chave  TEXT NOT NULL REFERENCES skill_nodes(chave),
    equipado    INTEGER NOT NULL DEFAULT 0 CHECK (equipado IN (0, 1)),
    PRIMARY KEY (player_id, node_chave)
);
CREATE TABLE IF NOT EXISTS playbooks (        -- defesa assíncrona: até 3 regras SE/ENTÃO
    player_id   INTEGER PRIMARY KEY REFERENCES players(id),
    regras_json TEXT NOT NULL DEFAULT '[]'
);

-- ---------------------------------------------- guerra, governo, Kage (18, 25-29)
CREATE TABLE IF NOT EXISTS war_points (
    semana      TEXT NOT NULL,
    village_id  INTEGER NOT NULL REFERENCES villages(id),
    player_id   INTEGER NOT NULL REFERENCES players(id),
    pontos      INTEGER NOT NULL DEFAULT 0 CHECK (pontos >= 0),
    dia         TEXT NOT NULL,
    PRIMARY KEY (semana, village_id, player_id, dia)
);
CREATE TABLE IF NOT EXISTS thrones (
    village_id  INTEGER PRIMARY KEY REFERENCES villages(id),
    kage_id     INTEGER REFERENCES players(id),
    desde       TEXT,
    escudo_ate  TEXT,
    numero      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS throne_challenges (
    id          INTEGER PRIMARY KEY,
    village_id  INTEGER NOT NULL REFERENCES villages(id),
    desafiante_id INTEGER NOT NULL REFERENCES players(id),
    kage_id     INTEGER REFERENCES players(id),
    vencedor_id INTEGER REFERENCES players(id),
    criado_em   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS reigns (
    id          INTEGER PRIMARY KEY,
    village_id  INTEGER NOT NULL REFERENCES villages(id),
    kage_id     INTEGER NOT NULL REFERENCES players(id),
    inicio      TEXT NOT NULL,
    fim         TEXT,
    desafios_vencidos INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS hall_of_kages (
    id          INTEGER PRIMARY KEY,
    village_id  INTEGER NOT NULL REFERENCES villages(id),
    player_nome TEXT NOT NULL,
    numero      INTEGER NOT NULL,
    discurso    TEXT CHECK (length(discurso) <= 140),
    dias_reinado INTEGER NOT NULL DEFAULT 0
);

-- --------------------------------------------------------- Sagas e história
CREATE TABLE IF NOT EXISTS sagas (
    id          INTEGER PRIMARY KEY,
    titulo      TEXT NOT NULL,
    inicio      TEXT NOT NULL,
    fim         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS saga_chapters (
    id          INTEGER PRIMARY KEY,
    saga_id     INTEGER NOT NULL REFERENCES sagas(id),
    ato         INTEGER NOT NULL CHECK (ato BETWEEN 1 AND 5),
    ordem       INTEGER NOT NULL,
    titulo      TEXT NOT NULL,
    texto       TEXT NOT NULL,
    gancho      TEXT
);
CREATE TABLE IF NOT EXISTS world_choices (
    id          INTEGER PRIMARY KEY,
    saga_id     INTEGER NOT NULL REFERENCES sagas(id),
    ato         INTEGER NOT NULL,
    opcao       TEXT NOT NULL,
    player_id   INTEGER NOT NULL REFERENCES players(id),
    UNIQUE (saga_id, ato, player_id)
);
CREATE TABLE IF NOT EXISTS world_chronicle (
    id          INTEGER PRIMARY KEY,
    texto       TEXT NOT NULL,
    criado_em   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- --------------------------------------------------- cosméticos, títulos, passe
CREATE TABLE IF NOT EXISTS titles (
    chave       TEXT PRIMARY KEY,
    nome        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS player_titles (
    player_id   INTEGER NOT NULL REFERENCES players(id),
    titulo_chave TEXT NOT NULL REFERENCES titles(chave),
    PRIMARY KEY (player_id, titulo_chave)
);
CREATE TABLE IF NOT EXISTS skins (
    chave       TEXT PRIMARY KEY,
    nome        TEXT NOT NULL,
    preco_ouro  INTEGER NOT NULL DEFAULT 0       -- só cosmético: nunca dá poder
);
CREATE TABLE IF NOT EXISTS player_skins (
    player_id   INTEGER NOT NULL REFERENCES players(id),
    skin_chave  TEXT NOT NULL REFERENCES skins(chave),
    equipada    INTEGER NOT NULL DEFAULT 0 CHECK (equipada IN (0, 1)),
    PRIMARY KEY (player_id, skin_chave)
);
CREATE TABLE IF NOT EXISTS player_pass (
    player_id   INTEGER NOT NULL REFERENCES players(id),
    temporada   INTEGER NOT NULL,
    trilha      TEXT NOT NULL DEFAULT 'free' CHECK (trilha IN ('free','elite')),
    progresso   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (player_id, temporada)
);

-- --------------------------------------------- governo e guerra (v1.3)
CREATE TABLE IF NOT EXISTS war_declarations (
    semana      TEXT NOT NULL,
    village_id  INTEGER NOT NULL REFERENCES villages(id),
    proposto_por INTEGER NOT NULL REFERENCES players(id),
    status      TEXT NOT NULL DEFAULT 'pendente' CHECK (status IN ('pendente','aprovada')),
    PRIMARY KEY (semana, village_id)
);
CREATE TABLE IF NOT EXISTS war_votes (
    semana      TEXT NOT NULL,
    village_id  INTEGER NOT NULL REFERENCES villages(id),
    player_id   INTEGER NOT NULL REFERENCES players(id),
    voto        INTEGER NOT NULL CHECK (voto IN (0, 1)),
    PRIMARY KEY (semana, village_id, player_id)
);
CREATE TABLE IF NOT EXISTS war_fights (
    id          INTEGER PRIMARY KEY,
    semana      TEXT NOT NULL,
    player_id   INTEGER NOT NULL REFERENCES players(id),
    village_id  INTEGER NOT NULL REFERENCES villages(id),
    venceu      INTEGER NOT NULL CHECK (venceu IN (0, 1)),
    pontos      INTEGER NOT NULL,
    criado_em   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS war_resolved (semana TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS war_dominio (
    village_id  INTEGER PRIMARY KEY REFERENCES villages(id),
    ate         TEXT NOT NULL,
    semana      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS no_confidence (
    reign_id    INTEGER NOT NULL REFERENCES reigns(id),
    player_id   INTEGER NOT NULL REFERENCES players(id),
    PRIMARY KEY (reign_id, player_id)
);

-- --------------------------------------------- jutsus, selos e builds (v1.4)
CREATE TABLE IF NOT EXISTS skill_state (
    player_id   INTEGER PRIMARY KEY REFERENCES players(id),
    respecs     INTEGER NOT NULL DEFAULT 0 CHECK (respecs >= 0),
    supremo     TEXT
);
CREATE TABLE IF NOT EXISTS player_selos (
    player_id   INTEGER NOT NULL REFERENCES players(id),
    chave       TEXT NOT NULL,
    qtd         INTEGER NOT NULL DEFAULT 0 CHECK (qtd >= 0),
    PRIMARY KEY (player_id, chave)
);
CREATE TABLE IF NOT EXISTS item_selos (
    inventory_id INTEGER NOT NULL REFERENCES inventory(id),
    chave       TEXT NOT NULL,
    PRIMARY KEY (inventory_id, chave)
);
INSERT OR IGNORE INTO schema_migrations (versao, nome) VALUES (3, 'jutsus_selos_v1_4');
INSERT OR IGNORE INTO schema_migrations (versao, nome) VALUES (1, 'schema_inicial_v1');
INSERT OR IGNORE INTO schema_migrations (versao, nome) VALUES (2, 'governo_guerra_v1_3');

-- v1.5: missão diária (aditivo, não apaga nada)
CREATE TABLE IF NOT EXISTS daily_missions (
    player_id   INTEGER NOT NULL REFERENCES players(id),
    dia         TEXT NOT NULL,
    vitorias    INTEGER NOT NULL DEFAULT 0 CHECK (vitorias >= 0),
    coletada    INTEGER NOT NULL DEFAULT 0 CHECK (coletada IN (0, 1)),
    PRIMARY KEY (player_id, dia)
);
INSERT OR IGNORE INTO schema_migrations (versao, nome) VALUES (4, 'missao_diaria_v1_5');
"""
