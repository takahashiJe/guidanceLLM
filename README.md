# 開発環境の起動:
docker compose --profile "*" up

# 本番環境での実行:
## API Gatewayと関連サービスのみを本番モードで起動(デーモン)
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile api up -d --build

## Workerと関連サービスのみを本番モードで起動(デーモン)
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile worker up -d --build