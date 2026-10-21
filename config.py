import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    DEBUG = os.getenv("DEBUG", "False").lower() in ("true", "1", "t")
    HOST = os.getenv("HOST", "0.0.0.0")
    PORT = int(os.getenv("PORT", 5000))
    DB_PATH = os.getenv("DB_PATH", "mysterious_kazan.db")
    ENV = os.getenv("ENV", "development")
    
    ENERGIA_MAX = 25
    ENERGIA_REGEN_MINUTOS = 6
    MOCHILA_BASE = 30

config = Config()
