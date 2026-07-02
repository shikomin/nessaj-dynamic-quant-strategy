from flask import Flask, jsonify

app = Flask(__name__)


def init_routes(collector, alpha_collector=None):
    @app.route("/status")
    def status():
        return jsonify({
            "service": "quant-scada-py",
            **collector.get_status()
        })

    @app.route("/health")
    def health():
        return jsonify({"status": "ok"})

    if alpha_collector is not None:
        @app.route("/fetch/alphafeed")
        def fetch_alphafeed():
            result = alpha_collector.collect_once()
            return jsonify(result)

        @app.route("/fetch/alphafeed/status")
        def fetch_alphafeed_status():
            return jsonify(alpha_collector.get_status())

    return app
