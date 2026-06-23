"""配置加载 — 所有脚本共用"""
import os
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_config(config_path: str = None) -> dict:
    if config_path is None:
        config_path = PROJECT_ROOT / "config" / "config.yaml"
    else:
        config_path = Path(config_path)

    if not config_path.exists():
        print(f"配置文件不存在: {config_path}", file=sys.stderr)
        sys.exit(1)

    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    env_password = os.getenv("TDENGINE_PASSWORD")
    if env_password:
        config['tdengine']['password'] = env_password

    return config
