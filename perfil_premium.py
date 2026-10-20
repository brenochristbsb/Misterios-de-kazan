import os
import requests
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy.orm import Session
from models import Player
from api import rank_titulo, effective_stats

def obter_avatar_circular(url_avatar: str, tamanho=(134, 134)) -> Image.Image:
    try:
        response = requests.get(url_avatar, timeout=5)
        img_quadrada = Image.open(BytesIO(response.content)).convert("RGBA")
    except Exception:
        img_quadrada = Image.new("RGBA", tamanho, color="#0c1017")
        draw = ImageDraw.Draw(img_quadrada)
        draw.ellipse([15, 15, tamanho-15, tamanho-15], fill="#1e2430")

    img_quadrada = img_quadrada.resize(tamanho, Image.Resampling.LANCZOS)
    mascara = Image.new("L", tamanho, 0)
    draw_mask = ImageDraw.Draw(mascara)
    draw_mask.ellipse([0, 0, tamanho, tamanho], fill=255)
    
    avatar_circular = Image.new("RGBA", tamanho, (0, 0, 0, 0))
    avatar_circular.paste(img_quadrada, (0, 0), mascara)
    return avatar_circular

def renderizar_perfil_ninja(db: Session, telegram_id: int, url_avatar: str = None) -> str:
    path_modelo = "modelo_ia.png"
    if not os.path.exists(path_modelo):
        raise FileNotFoundError("Por favor, garanta o arquivo 'modelo_ia.png' no diretório.")

    perfil = Image.open(path_modelo).convert("RGBA")
    draw = ImageDraw.Draw(perfil)

    player = db.query(Player).filter(Player.telegram_id == telegram_id).first()
    if not player:
        raise ValueError("Jogador não encontrado no banco de dados.")

    stats_calculados = effective_stats(player)
    titulo_patente = rank_titulo(player)
    nome_recurso = player.classe.recurso if player.classe else "chakra"

    fontes_sistema = ["DejaVuSans.ttf", "LiberationSans-Regular.ttf", "arial.ttf"]
    font_nome = font_sub = font_stats = ImageFont.load_default()
    for f in fontes_sistema:
        try:
            font_nome = ImageFont.truetype(f, 24)
            font_sub = ImageFont.truetype(f, 15)
            font_stats = ImageFont.truetype(f, 11)
            break
        except IOError:
            continue

    avatar_final = obter_avatar_circular(url_avatar, tamanho=(134, 134))
    perfil.paste(avatar_final, (42, 42), avatar_final)

    draw.text((295, 35), player.ninja_name.upper(), fill="#FFFFFF", font=font_nome)
    draw.text((295, 68), f"{titulo_patente} • Nível {player.level}", fill="#00E5FF", font=font_sub)

    hp_maximo = 100 + (stats_calculados.get("vigor", 5) * 5)
    recurso_maximo = 100 + (stats_calculados.get("inteligencia", 5) * 5) if nome_recurso == "chakra" else 100

    config_barras = [
        {"atual": player.vida_atual, "max": hp_maximo, "cor": "#FF3B30", "y": 194, "label": "HP"},
        {"atual": player.recurso_atual, "max": recurso_maximo, "cor": "#007AFF" if nome_recurso == "chakra" else "#FF9500", "y": 234, "label": nome_recurso.capitalize()},
        {"atual": player.energia, "max": 25, "cor": "#FFCC00", "y": 274, "label": "Energia"}
    ]

    largura_maxima_fenda = 180
    for barra in config_barras:
        max_v = barra["max"] if barra["max"] > 0 else 1
        proporcao = max(0.0, min(1.0, barra["atual"] / max_v))
        largura_atual = int(largura_maxima_fenda * proporcao)

        if largura_atual > 0:
            draw.rounded_rectangle([215, barra["y"], 215 + largura_atual, barra["y"] + 14], radius=6, fill=barra["cor"])

        texto_exibido = f"{barra['label']}: {barra['atual']}/{barra['max']}"
        draw.text((410, barra["y"] - 1), texto_exibido, fill="#FFFFFF", font=font_stats)

    path_saida = "perfil_dinamico_final.png"
    perfil.save(path_saida)
    return path_saida
