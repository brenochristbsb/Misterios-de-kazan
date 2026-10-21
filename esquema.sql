CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER UNIQUE,
    nome TEXT NOT NULL,
    aldeia_id TEXT,
    tradicao_id TEXT,
    nivel INTEGER DEFAULT 1,
    exp INTEGER DEFAULT 0,
    moedas INTEGER DEFAULT 100,
    moedas_eter INTEGER DEFAULT 0,
    energia INTEGER DEFAULT 25,
    ultimo_energia TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    codigo_convite TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS convites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    anfitriao_id INTEGER NOT NULL REFERENCES players(id),
    convidado_id INTEGER NOT NULL REFERENCES players(id),
    data TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    recompensa_paga INTEGER DEFAULT 0 CHECK(recompensa_paga IN (0,1)),
    UNIQUE(convidado_id)
);

CREATE TABLE IF NOT EXISTS progresso_historia (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER NOT NULL REFERENCES players(id),
    saga_id TEXT NOT NULL,
    ultimo_capitulo TEXT NOT NULL,
    concluido INTEGER DEFAULT 0 CHECK(concluido IN (0,1)),
    desbloqueado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(player_id, saga_id)
);

CREATE TABLE IF NOT EXISTS conquistas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chave TEXT UNIQUE NOT NULL,
    nome TEXT NOT NULL,
    descricao TEXT NOT NULL,
    icone TEXT DEFAULT '🏆',
    recompensa_tipo TEXT,
    recompensa_valor INTEGER
);

CREATE TABLE IF NOT EXISTS conquistas_jogador (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER NOT NULL REFERENCES players(id),
    conquista_id INTEGER NOT NULL REFERENCES conquistas(id),
    conquistado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(player_id, conquista_id)
);

INSERT OR IGNORE INTO conquistas(chave, nome, descricao, icone, recompensa_tipo, recompensa_valor) VALUES
('primeira_vitoria', 'Primeiro Passo', 'Vença sua primeira batalha', '⚔️', 'moedas_eter', 10),
('explorador', 'Explorador Iniciante', 'Explore 5 vezes', '🗺️', 'moedas_eter', 20),
('convidado', 'Anfitrião', 'Convide um amigo para o jogo', '🤝', 'moedas_eter', 50),
('nivel_5', 'Caminho do Aprendiz', 'Alcance o nível 5', '⭐', 'moedas_eter', 30);
