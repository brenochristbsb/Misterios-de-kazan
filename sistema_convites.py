import uuid
from datetime import datetime

def gerar_codigo_convite(db, jogador_id):
    jogador_id = int(jogador_id)
    codigo = str(uuid.uuid4())[:8].upper()
    db.execute(
        "UPDATE players SET codigo_convite = ? WHERE id = ?",
        (codigo, jogador_id)
    )
    return codigo

def obter_codigo_convite(db, jogador_id):
    jogador_id = int(jogador_id)
    res = db.execute(
        "SELECT codigo_convite FROM players WHERE id = ?",
        (jogador_id,)
    ).fetchone()
    if res and res[0]:
        return res[0]
    return gerar_codigo_convite(db, jogador_id)

def resgatar_convite(db, convidado_id, codigo):
    convidado_id = int(convidado_id)
    codigo = codigo.strip().upper()
    res = db.execute(
        "SELECT id FROM players WHERE codigo_convite = ? AND id != ?",
        (codigo, convidado_id)
    ).fetchone()
    if not res:
        return {"sucesso": False, "mensagem": "Código inválido"}
    
    anfitriao_id = res[0]
    ja_resgatou = db.execute(
        "SELECT 1 FROM convites WHERE convidado_id = ?",
        (convidado_id,)
    ).fetchone()
    if ja_resgatou:
        return {"sucesso": False, "mensagem": "Você já usou um convite"}
    
    db.execute("""
        INSERT INTO convites(anfitriao_id, convidado_id, data, recompensa_paga)
        VALUES (?, ?, ?, 1)
    """, (anfitriao_id, convidado_id, datetime.now()))
    
    db.execute("UPDATE players SET moedas_eter = moedas_eter + 50 WHERE id = ?", (anfitriao_id,))
    db.execute("UPDATE players SET moedas_eter = moedas_eter + 30 WHERE id = ?", (convidado_id,))
    
    return {"sucesso": True, "mensagem": "🎉 Convite resgatado!"}

def contar_convites(db, jogador_id):
    jogador_id = int(jogador_id)
    return db.execute(
        "SELECT COUNT(*) FROM convites WHERE anfitriao_id = ?",
        (jogador_id,)
    ).fetchone()[0]
