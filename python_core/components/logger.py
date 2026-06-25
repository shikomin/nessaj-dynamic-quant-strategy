"""日志配置 — 所有脚本共用"""
import sys
import logging
from pathlib import Path

from components.config import PROJECT_ROOT


def setup_logging(config: dict, log_name: str = "app.log"):
    log_cfg = config.get('logger', {})
    level = getattr(logging, log_cfg.get('level', 'INFO').upper(), logging.INFO)
    log_file = PROJECT_ROOT / "data" / "logs" / log_name
    log_file.parent.mkdir(parents=True, exist_ok=True)

    logging.getLogger().handlers.clear()
    logging.basicConfig(
        level=level,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[
            logging.FileHandler(str(log_file), encoding='utf-8'),
            logging.StreamHandler(sys.stdout),
        ]
    )
