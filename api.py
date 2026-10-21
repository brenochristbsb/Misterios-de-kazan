from flask import Flask, jsonify, request
import os

app = Flask(__name__)

# Banco de dados simples em memória
players = {}

@app.route('/')
def home():
    return jsonify({
        "mensagem": "Bem-vindo aos Mistérios de Kazan!",
        "status": "online"
    })

@app.route('/api/criar-jogador', methods=['POST'])
def criar_jogador():
    dados = request.get_json()
    nome = dados.get('nome')
    if not nome:
        return jsonify({"erro": "Nome é obrigatorio"}), 400
    
    jogador_id = len(players) + 1
    players[jogador_id] = {
        "id": jogador_id,
        "nome": nome,
        "nivel": 1,
        "experiencia": 0
    }
    return jsonify(players[jogador_id]), 201

@app.route('/api/jogador/<int:jogador_id>', methods=['GET'])
def buscar_jogador(jogador_id):
    if jogador_id not in players:
        return jsonify({"erro": "Jogador nao encontrado"}), 404
    return jsonify(players[jogador_id])

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
