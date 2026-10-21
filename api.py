from flask import Flask, jsonify

app = Flask(__name__)

@app.route('/')
def home():
    return jsonify({"mensagem": "Bem-vindo aos Misterios de Kazan!", "status": "online"})

@app.route('/api/saudacao')
def saudacao():
    return jsonify({"resposta": "Ola! Tudo bem por aqui?"})

if __name__ == '__main__':
    app.run()
