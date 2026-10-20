-- Bloco 3 (Marionetista e Ninja Médico). Seguro para jogadores existentes: só ADICIONA colunas com default.
-- ATENÇÃO: troque "players" pelo __tablename__ real do Player (models.py não veio no envio).
-- Se você adicionar as colunas ao models.py (ver models_patch.txt), o ensure_columns() do api.py já cria tudo sozinho
-- no próximo start; este SQL serve para rodar a migração manualmente/em staging. Não rode os dois: o ensure_columns pula colunas existentes.

-- SQLite
ALTER TABLE players ADD COLUMN marionete JSON DEFAULT '{}';
ALTER TABLE players ADD COLUMN passiva_medica VARCHAR(16) DEFAULT '';

-- PostgreSQL (use no lugar das duas linhas acima)
-- ALTER TABLE players ADD COLUMN IF NOT EXISTS marionete JSON NOT NULL DEFAULT '{}'::json;
-- ALTER TABLE players ADD COLUMN IF NOT EXISTS passiva_medica VARCHAR(16) NOT NULL DEFAULT '';

-- Jogadores antigos: marionete = {} é normalizada na primeira vez que o jogador usa a classe
-- (classes_bloco3.normalizar cria a marionete com HP cheio). Nenhum UPDATE necessário.
-- Rollback: ALTER TABLE players DROP COLUMN marionete; ALTER TABLE players DROP COLUMN passiva_medica;
