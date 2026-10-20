import os
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy.orm import Session
from models import Player
from jutsus_dados import JUTSUS, MARCOS, ELEMENTOS
from api import rank_titulo, effective_stats

def renderizar_aba_jutsus(db: Session, telegram_id: int) -> str:
    path_modelo = "modelo_ia.png"
    if not os.path.exists(path_modelo):
        raise FileNotFoundError("Por favor, garanta o arquivo de arte base no diretório.")

    perfil = Image.open(path_modelo).convert("RGBA")
    draw = ImageDraw.Draw(perfil)

    player = db.query(Player).filter(Player.telegram_id == telegram_id).first()
    if not player:
        raise ValueError("Ninja não encontrado no banco de dados.")

    stats_efetivos = effective_stats(player)
    titulo_patente = rank_titulo(player)

    fontes_sistema = ["DejaVuSans.ttf", "LiberationSans-Regular.ttf", "arial.ttf"]
    font_titulo = font_sub = font_texto = ImageFont.load_default()
    for f in fontes_sistema:
        try:
            font_titulo = ImageFont.truetype(f, 22)
            font_sub = ImageFont.truetype(f, 15)
            font_texto = ImageFont.truetype(f, 12)
            break
        except IOError:
            continue

    elem_info = ELEMENTOS.get(player.elemento, ("Estilo Puro", "🥷"))[0]
    draw.text((45, 45), f"AFINIDADE: {elem_info.upper()}", fill="#00E5FF", font=font_sub)
    
    if player.tecnica:
        draw.text((45, 70), f"Secreta: {player.tecnica}", fill="#FF9500", font=font_texto)

    draw.text((295, 35), "ÁRVORE DE JUTSUS", fill="#FFFFFF", font=font_titulo)
    draw.text((295, 68), f"{titulo_patente} • Nível {player.level}", fill="#A0A5B5", font=font_sub)

    pos_y_inicial = 140
    espacamento_y = 95
    jutsus_ninja = player.jutsus or {}

    for i, marco_nv in enumerate(MARCOS):
        y_atual = pos_y_inicial + (i * espacamento_y)
        jutsu_id = jutsus_ninja.get(str(marco_nv))
        
        draw.rectangle([45, y_atual, 555, y_atual + 75], outline="#2d323f", width=1)

        if jutsu_id and jutsu_id in JUTSUS:
            dados_jutsu = JUTSUS[jutsu_id]
            nome_display = f"Tier {i+1}: {dados_jutsu['nome']}"
            desc_display = f"Categoria: {dados_jutsu['cat'].capitalize()} | Poder: {int(dados_jutsu['poder'] * 100)}%"
            draw.text((65, y_atual + 15), nome_display, fill="#FFFFFF", font=font_sub)
            draw.text((65, y_atual + 42), desc_display, fill="#A0A5B5", font=font_texto)
            draw.text((430, y_atual + 15), "⚡ APRENDIDO", fill="#00E5FF", font=font_texto)
        elif player.level >= marco_nv:
            draw.text((65, y_atual + 15), f"Marco do Nível {marco_nv}: Disponível", fill="#FFCC00", font=font_sub)
            draw.text((65, y_atual + 42), "Acesse os botões no bot para escolher sua habilidade.", fill="#5a5e6c", font=font_texto)
            draw.text((450, y_atual + 15), "🟢 DISPONÍVEL", fill="#FFCC00", font=font_texto)
        else:
            draw.text((65, y_atual + 15), "Marco Oculto", fill="#5a5e6c", font=font_sub)
            draw.text((65, y_atual + 42), f"Requer evolução até o nível {marco_nv}.", fill="#424651", font=font_texto)
            draw.text((460, y_atual + 15), "🔒 BLOQUEADO", fill="#5a5e6c", font=font_texto)

    path_saida = "aba_jutsus_dinamica.png"
    perfil.save(path_saida)
    return path_saida
