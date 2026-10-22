import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
import unittest
from urllib.parse import urlencode

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["BOT_TOKEN"] = "123:TESTE"
os.environ.pop("DATABASE_URL", None)
os.environ.pop("WEBHOOK_URL", None)
os.environ.pop("RENDER_EXTERNAL_URL", None)
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "teste.db")

from wsgi import app  # noqa: E402
import banco  # noqa: E402


def cabecalho(uid, nome="Teste"):
    d = {"auth_date": str(int(time.time())), "user": json.dumps({"id": uid, "first_name": nome})}
    texto = "\n".join(f"{k}={v}" for k, v in sorted(d.items()))
    chave = hmac.new(b"WebAppData", b"123:TESTE", hashlib.sha256).digest()
    d["hash"] = hmac.new(chave, texto.encode(), hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data": urlencode(d)}


class Base(unittest.TestCase):
    uid = 1000

    @classmethod
    def setUpClass(cls):
        cls.c = app.test_client()

    def setUp(self):
        type(self).uid += 1
        self.h = cabecalho(self.uid)

    def get(self, url):
        return self.c.get(url, headers=self.h).get_json()

    def post(self, url, corpo=None):
        r = self.c.post(url, json=corpo or {}, headers=self.h)
        return r.status_code, r.get_json()

    def criar(self, moedas=None, nivel=None):
        self.get("/api/jogador")
        self.post("/api/perfil", {"aldeia_id": "floresta", "tradicao_id": "guerreiro"})
        with banco.db() as con:
            if moedas is not None:
                con.execute("UPDATE players SET moedas = ? WHERE telegram_id = ?", (moedas, self.uid))
            if nivel is not None:
                con.execute("UPDATE players SET nivel = ? WHERE telegram_id = ?", (nivel, self.uid))


class TestSeguranca(Base):
    def test_sem_login(self):
        self.assertEqual(self.c.get("/api/jogador").status_code, 401)

    def test_assinatura_falsa(self):
        h = cabecalho(self.uid)
        h["X-Telegram-Init-Data"] = h["X-Telegram-Init-Data"].replace("hash=", "hash=0")
        self.assertEqual(self.c.get("/api/jogador", headers=h).status_code, 401)

    def test_webhook_exige_segredo(self):
        self.assertEqual(self.c.post("/webhook", json={}).status_code, 403)

    def test_limite_de_acoes(self):
        self.criar()
        codigos = [self.post("/api/banco/converter", {"qtd": 1})[0] for _ in range(45)]
        self.assertIn(429, codigos)

    def test_saude(self):
        self.assertEqual(self.c.get("/saude").status_code, 200)


class TestJogo(Base):
    def test_perfil_so_uma_vez(self):
        self.get("/api/jogador")
        self.assertEqual(self.post("/api/perfil", {"aldeia_id": "floresta", "tradicao_id": "guerreiro"})[0], 200)
        self.assertEqual(self.post("/api/perfil", {"aldeia_id": "rocha", "tradicao_id": "guerreiro"})[0], 403)

    def test_capitulo_bloqueado_e_recompensa_unica(self):
        self.criar()
        self.assertEqual(self.post("/api/saga/caminho_do_eter/capitulo/cap2/concluir")[0], 403)
        _, r1 = self.post("/api/saga/caminho_do_eter/capitulo/cap1/concluir")
        _, r2 = self.post("/api/saga/caminho_do_eter/capitulo/cap1/concluir")
        self.assertIsNotNone(r1["recompensa"])
        self.assertIsNone(r2["recompensa"])
        self.assertEqual(r1["jogador"]["exp"], r2["jogador"]["exp"])

    def test_item_exige_nivel(self):
        self.criar(moedas=100000, nivel=1)
        self.assertEqual(self.post("/api/mercado/comprar", {"id": "arma_20_0"})[0], 400)
        self.assertEqual(self.post("/api/mercado/comprar", {"id": "arma_1_0"})[0], 200)

    def test_compra_e_venda(self):
        self.criar(moedas=1000)
        _, c = self.post("/api/mercado/comprar", {"id": "arma_1_0"})
        _, v = self.post("/api/mercado/vender", {"id": "arma_1_0"})
        self.assertEqual(c["jogador"]["moedas"], 940)
        self.assertEqual(v["jogador"]["moedas"], 970)   # venda = metade do preço

    def test_equipado_nao_vende(self):
        self.criar(moedas=1000)
        self.post("/api/mercado/comprar", {"id": "arma_1_0"})
        self.post("/api/inventario/equipar", {"id": "arma_1_0"})
        self.assertEqual(self.post("/api/mercado/vender", {"id": "arma_1_0"})[0], 400)

    def test_explorar_gasta_energia(self):
        self.criar()
        estados = [self.post("/api/portal/explorar")[0] for _ in range(6)]
        self.assertEqual(estados[:5], [200] * 5)   # 25 de energia = 5 explorações
        self.assertEqual(estados[5], 400)

    def test_dinheiro_nunca_negativo(self):
        self.criar(moedas=10)
        self.assertEqual(self.post("/api/banco/converter", {"qtd": 5})[0], 400)
        self.assertEqual(self.get("/api/jogador")["jogador"]["moedas"], 10)


if __name__ == "__main__":
    unittest.main()


class TestNucleo(Base):
    def test_curva_de_niveis(self):
        self.assertEqual(banco.nivel_para_exp(0), 1)
        self.assertEqual(banco.nivel_para_exp(banco.exp_total(50)), 50)
        self.assertEqual(banco.nivel_para_exp(10 ** 9), 100)
        self.assertTrue(all(banco.exp_total(n + 1) > banco.exp_total(n) for n in range(1, 100)))

    def test_historia_exige_nivel(self):
        self.criar()
        self.post("/api/saga/caminho_do_eter/capitulo/cap1/concluir")
        self.post("/api/saga/caminho_do_eter/capitulo/cap2/concluir")
        self.assertEqual(self.post("/api/saga/caminho_do_eter/capitulo/cap3/concluir")[0], 403)   # precisa de nível 3

    def test_arena_luta_e_gasta_energia(self):
        self.criar()
        antes = self.get("/api/jogador")["jogador"]["energia"]
        status, r = self.post("/api/arena/lutar", {"id": "b1"})
        self.assertEqual(status, 200)
        self.assertTrue(r["rodadas"])
        self.assertEqual(r["jogador"]["energia"], antes - 4)
        self.assertGreaterEqual(r["trofeus"], 0)

    def test_arena_recusa_oponente_invalido(self):
        self.criar()
        self.assertEqual(self.post("/api/arena/lutar", {"id": "x9"})[0], 400)
        self.assertEqual(self.post("/api/arena/lutar", {"id": "p999999"})[0], 400)

    def test_combate_forte_vence_fraco(self):
        fraco = {"ataque": 10, "defesa": 5, "vida": 100}
        forte = {"ataque": 200, "defesa": 100, "vida": 2000}
        self.assertTrue(banco.simular_combate(forte, fraco)[0])
        self.assertFalse(banco.simular_combate(fraco, forte)[0])


class TestMasmorra(Base):
    def token(self, jid, inicio, nonce="abc123"):
        from api import CHAVE_RUN
        msg = f"{jid}.1.{inicio}.{nonce}"
        return msg + "." + hmac.new(CHAVE_RUN, msg.encode(), hashlib.sha256).hexdigest()[:24]

    def test_iniciar_gasta_energia_e_devolve_token(self):
        self.criar()
        s, r = self.post("/api/masmorra/iniciar", {"dif": 1})
        self.assertEqual(s, 200)
        self.assertEqual(r["jogador"]["energia"], 19)
        self.assertEqual(self.post("/api/masmorra/iniciar", {"dif": 9})[0], 400)

    def test_token_falso_e_reuso(self):
        self.criar()
        jid = self.get("/api/jogador")["jogador"]["id"]
        self.assertEqual(self.post("/api/masmorra/finalizar", {"token": "1.1.1.x.y", "ondas": 10})[0], 403)
        t = self.token(jid, int(time.time()) - 400)
        self.assertEqual(self.post("/api/masmorra/finalizar", {"token": t, "ondas": 3})[0], 200)
        self.assertEqual(self.post("/api/masmorra/finalizar", {"token": t, "ondas": 3})[0], 403)   # só vale uma vez

    def test_recompensa_limitada_pelo_tempo(self):
        self.criar()
        jid = self.get("/api/jogador")["jogador"]["id"]
        _, r = self.post("/api/masmorra/finalizar", {"token": self.token(jid, int(time.time()) - 5, "n1"), "ondas": 10, "venceu": True})
        self.assertEqual(r["ondas"], 0)      # 5 segundos não bastam para nenhuma onda
        self.assertFalse(r["venceu"])
        _, r = self.post("/api/masmorra/finalizar", {"token": self.token(jid, int(time.time()) - 35, "n2"), "ondas": 10, "venceu": True})
        self.assertEqual(r["ondas"], 3)      # 35 s permitem no máximo 3 ondas
        _, r = self.post("/api/masmorra/finalizar", {"token": self.token(jid, int(time.time()) - 600, "n3"), "ondas": 10, "venceu": True})
        self.assertTrue(r["venceu"])
        self.assertGreater(r["recompensa"]["exp"], 0)

    def test_token_de_outro_jogador(self):
        self.criar()
        t = self.token(999999, int(time.time()) - 400, "n4")
        self.assertEqual(self.post("/api/masmorra/finalizar", {"token": t, "ondas": 5})[0], 403)
