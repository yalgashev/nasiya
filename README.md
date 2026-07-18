# Nasiya

Nasiya is a mobile-first web application for managing nasiya workflows.
It is designed as a single browser-based product for shop and customer use.

## Docker orqali ishga tushirish

```bash
cp .env.example .env
docker compose up --build -d
docker compose ps
curl http://localhost:8000/health
```

Loglarni ko'rish:

```bash
docker compose logs -f web
```

Alembic migratsiyalarini qo'llash:

```bash
docker compose exec web alembic upgrade head
```

## PostgreSQL dump importi

Oddiy UTF-8 SQL dump uchun:

```bash
docker compose exec -T db psql \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set ON_ERROR_STOP=on < /path/to/nasiya_dump.sql
```

Windows PowerShell UTF-16 formatida saqlagan dump uchun avval kodlashni
o'zgartirib, keyin import qiling:

```bash
iconv -f UTF-16LE -t UTF-8 /path/to/nasiya_dump.sql \
  | sed '1s/^\xEF\xBB\xBF//' \
  | docker compose exec -T db psql \
      --username "$POSTGRES_USER" \
      --dbname "$POSTGRES_DB" \
      --set ON_ERROR_STOP=on
```

`.env` yuklanmagan terminalda standart qiymatlar `nasiya`, `nasiya` va
`dev_pass` hisoblanadi. Yangi dump olishda PowerShell redirection o'rniga
`pg_dump --file=nasiya_dump.sql ...` dan foydalaning.
