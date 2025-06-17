#!/bin/sh
# 最初にDB初期化スクリプトを実行
python -m app.init_db_script
# その後、本来のCMDであるCeleryワーカーを起動
exec celery -A shared.celery_app worker --loglevel=info