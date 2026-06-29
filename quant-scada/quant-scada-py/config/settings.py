import os


class Config:
    ZZSHARE_BASE_URL = os.getenv("ZZSHARE_BASE_URL", "https://api.zizizaizai.com")
    ZZSHARE_TOKEN = os.getenv("ZZSHARE_TOKEN", "anonymous")

    TD_DSN = os.getenv("TD_DSN", "taosws://root:Nessaj@111@124.221.130.19:6041")

    FLASK_HOST = os.getenv("FLASK_HOST", "0.0.0.0")
    FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))

    COLLECT_INTERVAL = int(os.getenv("COLLECT_INTERVAL_SECONDS", "20"))
    BATCH_COUNT = int(os.getenv("BATCH_COUNT", "20"))
    RT_DATABASE = os.getenv("RT_DATABASE", "quant_scada_rt")
    HIST_DATABASE = os.getenv("HIST_DATABASE", "quant_scada_hist")

    INDEX_CODES = [c.strip() for c in os.getenv("INDEX_CODES", "").split(",") if c.strip()]
    STOCK_CODES = [c.strip() for c in os.getenv("STOCK_CODES", "").split(",") if c.strip()]

    MYSQL_HOST = os.getenv("MYSQL_HOST", "124.221.130.19")
    MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
    MYSQL_USER = os.getenv("MYSQL_USER", "root")
    MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "Password@111")
    MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "quant_scada")
