from flask import Flask, jsonify

def create_app():
    app = Flask(__name__)

    @app.route('/')
    def home():
        return jsonify({"mensagem": "Bem-vindo aos Misterios de Kazan!", "status": "online"})

    return app
