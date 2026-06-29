from flask import Flask, jsonify

app = Flask(__name__)


def init_routes(collector):
    @app.route("/status")
    def status():
        return jsonify({
            "service": "quant-scada-py",
            **collector.get_status()
        })

    @app.route("/health")
    def health():
        return jsonify({"status": "ok"})

    return app
