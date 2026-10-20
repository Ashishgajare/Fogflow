from pathlib import Path

from flask import Flask, jsonify
from flask_cors import CORS

from simulation.benchmark import BenchmarkManager
from simulation.database import SimulationDatabase
from simulation.engine import SimulationEngine
from simulation.routes import api


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(
        DATABASE=str(Path(app.root_path) / "instance" / "fogflow.sqlite3"),
        BACKGROUND_SIMULATION=True,
        ADAPTIVE_WEIGHTS={
            "response_time": 0.4,
            "utilization": 0.35,
            "completion_time": 0.25,
        },
        NODE_CONFIGS=None,
    )
    if test_config:
        app.config.update(test_config)

    CORS(
        app,
        resources={
            r"/api/*": {
                "origins": [
                    "http://localhost:5173",
                    "http://127.0.0.1:5173",
                ]
            }
        },
    )

    database = SimulationDatabase(app.config["DATABASE"])
    benchmarks = BenchmarkManager(
        database=database,
        node_configs=app.config["NODE_CONFIGS"],
        weights=app.config["ADAPTIVE_WEIGHTS"],
        logger=app.logger,
    )
    engine = SimulationEngine(
        database=database,
        node_configs=app.config["NODE_CONFIGS"],
        adaptive_weights=app.config["ADAPTIVE_WEIGHTS"],
        background_simulation=app.config["BACKGROUND_SIMULATION"],
    )
    app.extensions["fogflow_database"] = database
    app.extensions["fogflow_engine"] = engine
    app.extensions["fogflow_benchmarks"] = benchmarks

    app.register_blueprint(api, url_prefix="/api")

    @app.get("/api/health")
    def health():
        return jsonify(status="ok")

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
