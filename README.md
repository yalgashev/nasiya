# Nasiya

Nasiya is a mobile-first web application for managing nasiya workflows.
It is designed as a single browser-based product for shop and customer use.

## Windows prerequisites

Windowsda ishlash uchun quyidagilar kerak:

- Git o'rnatilgan va `PATH`da mavjud bo'lishi kerak.
- uv o'rnatilgan bo'lishi kerak; loyiha Python versiyasini `.python-version`
  orqali `3.12`ga mahkamlaydi, shuning uchun Pythonni alohida qo'lda
  o'rnatish shart emas, uv uni boshqarishi mumkin.
- Docker Desktop o'rnatilgan va ishga tushgan bo'lishi kerak.
- PowerShell orqali loyiha buyruqlari repository ildizidan bajariladi.

## Birinchi ishga tushirish (Windows PowerShell)

```powershell
cd C:\path\to\nasiya
Copy-Item .env.example .env
docker compose config
docker compose build web
docker compose up -d
docker compose exec web alembic upgrade head
Start-Process http://localhost:8000/
Start-Process http://localhost:8000/health
```

## Birinchi ishga tushirish (Xubuntu Terminal)

```bash
cd /home/yalgashev/projects/nasiya
cp .env.example .env
docker compose config
docker compose build web
docker compose up -d
docker compose exec web alembic upgrade head
xdg-open http://localhost:8000/
xdg-open http://localhost:8000/health
```

Loglarni ko'rish:

```bash
docker compose logs -f web
```

Servislar holatini ko'rish:

```bash
docker compose ps
```

## Tekshiruvlar (Windows PowerShell)

```powershell
cd C:\path\to\nasiya
uv sync --dev --frozen
docker compose config
uv run ruff check .
$env:TEST_DATABASE_URL = "postgresql+psycopg://nasiya:dev_pass@127.0.0.1:5432/nasiya"
uv run pytest -ra
```

## Tekshiruvlar (Xubuntu Terminal)

```bash
cd /home/yalgashev/projects/nasiya
uv sync --dev --frozen
docker compose config
uv run ruff check .
TEST_DATABASE_URL='postgresql+psycopg://nasiya:dev_pass@127.0.0.1:5432/nasiya' uv run pytest -ra
```

## To'xtatish (Windows PowerShell)

Containerlarni to'xtatish uchun:

```powershell
docker compose down
```

Bu buyruq container va networkni to'xtatadi, lekin PostgreSQL ma'lumotlari
named volume ichida saqlanib qoladi.

XAVFLI: containerlar bilan birga PostgreSQL ma'lumotlarini ham o'chirish:

```powershell
docker compose down -v
```

`-v` varianti named volume'ni ham o'chiradi. Bu database ichidagi lokal
ma'lumotlarni yo'q qiladi.

## To'xtatish (Xubuntu Terminal)

Containerlarni to'xtatish uchun:

```bash
docker compose down
```

Bu buyruq container va networkni to'xtatadi, lekin PostgreSQL ma'lumotlari
named volume ichida saqlanib qoladi.

XAVFLI: containerlar bilan birga PostgreSQL ma'lumotlarini ham o'chirish:

```bash
docker compose down -v
```

`-v` varianti named volume'ni ham o'chiradi. Bu database ichidagi lokal
ma'lumotlarni yo'q qiladi.

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
