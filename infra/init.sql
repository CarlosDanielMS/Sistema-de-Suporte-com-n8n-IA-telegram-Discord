-- Criação da tabela de tickets do sistema de suporte IA
-- Executado automaticamente pelo PostgreSQL na primeira inicialização

CREATE TABLE IF NOT EXISTS tickets (
    id               SERIAL PRIMARY KEY,
    timestamp        TEXT,
    usuario          TEXT,
    pergunta         TEXT,
    resposta         TEXT,
    urgencia         TEXT,
    fontes           TEXT,
    latencia_s       NUMERIC(6,2),
    tools_utilizadas TEXT,
    criado_em        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tickets_urgencia  ON tickets (urgencia);
CREATE INDEX IF NOT EXISTS idx_tickets_usuario   ON tickets (usuario);
CREATE INDEX IF NOT EXISTS idx_tickets_criado_em ON tickets (criado_em DESC);
