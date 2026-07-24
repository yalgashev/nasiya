# Nasiya

Nasiya is a mobile-first web application for managing nasiya workflows.
It is designed as a single browser-based product for shop and customer use.

## Prerequisites

Windows uchun:

- Git `PATH`da mavjud bo'lishi kerak.
- Docker Desktop o'rnatilgan va ishga tushgan bo'lishi kerak.
- uv o'rnatilgan bo'lishi kerak. Loyiha Python versiyasini
  `.python-version` orqali `3.12`ga mahkamlaydi.
- Buyruqlar Windows PowerShell orqali repository ildizidan bajariladi.

Xubuntu uchun:

- Git, Docker va uv mavjud bo'lishi kerak.
- Foydalanuvchi Docker buyruqlarini bajarish huquqiga ega bo'lishi kerak.
- Buyruqlar Terminal orqali repository ildizidan bajariladi.

## Environment

Local sozlash uchun `.env.example`dan boshlang:

```bash
cp .env.example .env
```

PowerShell:

```powershell
Copy-Item .env.example .env
```

Muhim maydonlar:

- `DATABASE_URL` - local development database, odatda `nasiya`.
- `TEST_DATABASE_URL` - alohida local test database, `nasiya_test`.
- `SESSION_COOKIE_NAME` - default `nasiya_session`.
- `SESSION_COOKIE_SECURE` - local HTTP development uchun `false`.
- `SESSION_TTL_DAYS`, `ANONYMOUS_SESSION_TTL_MINUTES`,
  `SESSION_TOUCH_INTERVAL_MINUTES` - server-side session muddatlari.
- `PASSWORD_MIN_LENGTH`, `PASSWORD_MAX_LENGTH` - parol siyosati.
- `LOGIN_RATE_LIMIT_WINDOW_SECONDS`, `LOGIN_RATE_LIMIT_PHONE_ATTEMPTS`,
  `LOGIN_RATE_LIMIT_IP_ATTEMPTS` - auth rate-limit sozlamalari.
- `RATE_LIMIT_HMAC_KEY` - raw phone/IP ni DBga yozmaslik uchun HMAC secret.

`RATE_LIMIT_HMAC_KEY`ning real qiymatini README, CI log, commit yoki chatda
chiqarmang. `.env.example` faqat development namunasi; productionda alohida,
kamida 32 belgili maxfiy qiymat bering.

Production HTTPS muhitida `SESSION_COOKIE_SECURE=true` bo'lishi shart.

## Databases

Development va test bazalari alohida bo'lishi kerak:

- development database: `nasiya`
- local test database: `nasiya_test`
- CI test database: `nasiya_test`

Test database nomi `_test` bilan tugashi shart. Testlar SQLite URLni va
development databasega qaragan `TEST_DATABASE_URL`ni rad etadi.

## First Run (Xubuntu Terminal)

```bash
cd /home/yalgashev/projects/nasiya
cp .env.example .env
docker compose config --quiet
docker compose build web
docker compose up -d
docker compose exec web alembic upgrade head
xdg-open http://localhost:8000/
xdg-open http://localhost:8000/auth/login
```

Local test DB URLni alohida bering:

```bash
export TEST_DATABASE_URL='postgresql+psycopg://nasiya:dev_pass@127.0.0.1:5432/nasiya_test'
```

## First Run (Windows PowerShell)

```powershell
cd C:\path\to\nasiya
Copy-Item .env.example .env
docker compose config --quiet
docker compose build web
docker compose up -d
docker compose exec web alembic upgrade head
Start-Process http://localhost:8000/
Start-Process http://localhost:8000/auth/login
```

Local test DB URLni alohida bering:

```powershell
$env:TEST_DATABASE_URL = "postgresql+psycopg://nasiya:dev_pass@127.0.0.1:5432/nasiya_test"
```

## Migrations

Container ichida development database uchun:

```bash
docker compose exec web alembic upgrade head
docker compose exec web alembic current
```

Hostdan test database uchun:

```bash
TEST_DATABASE_URL='postgresql+psycopg://nasiya:dev_pass@127.0.0.1:5432/nasiya_test' \
  uv run alembic upgrade head
TEST_DATABASE_URL='postgresql+psycopg://nasiya:dev_pass@127.0.0.1:5432/nasiya_test' \
  uv run alembic current
```

PowerShell:

```powershell
$env:TEST_DATABASE_URL = "postgresql+psycopg://nasiya:dev_pass@127.0.0.1:5432/nasiya_test"
uv run alembic upgrade head
uv run alembic current
```

M3 checkpointida `alembic current` natijasi `b1f3a7c9d2e4 (head)` bo'lishi
kerak. Migrationni development databasega container ichidan, test databasega
esa faqat `_test` bilan tugaydigan alohida `TEST_DATABASE_URL` orqali qo'llang.

## Local User

Local/dev muhitda parol bilan kirish uchun user yarating:

```bash
docker compose exec web python -m app.cli create-local-user --phone +998901234567
```

PowerShell:

```powershell
docker compose exec web python -m app.cli create-local-user --phone +998901234567
```

Parol terminalda ikki marta hidden prompt orqali so'raladi. Raw passwordni
command-line argument, README, log yoki chatga yozmang. Production muhitida bu
CLI fail-closed ishlaydi.

## Auth URLs

Local web server:

- `http://localhost:8000/auth/login`
- `http://localhost:8000/auth/account`
- `http://localhost:8000/auth/sessions`

`/auth/account` va `/auth/sessions` authenticated session talab qiladi.

## Customer Draft Foundation (M3)

M3 faqat authenticated customer onboarding qoralamasi uchun foundation
yaratadi. Bu public ro'yxatdan o'tish emas; customer faqat draft holatida
qoladi.

Customer jadvali PII saqlamaydi. Telefon auth userda qoladi va customer
profilida faqat maskalangan ko'rinishda chiqadi.

Local web server:

- `http://localhost:8000/customer/onboarding`
- `http://localhost:8000/customer/profile`

Local user yaratib `/auth/login` orqali kiring, so'ng `/auth/account`dagi
customer draft onboarding linkini oching. Bu URLlar faqat authenticated draft
sahifalaridir.

## Validation (Xubuntu Terminal)

```bash
cd /home/yalgashev/projects/nasiya
uv sync --dev --frozen
docker compose config --quiet
TEST_DATABASE_URL='postgresql+psycopg://nasiya:dev_pass@127.0.0.1:5432/nasiya_test' \
  uv run alembic upgrade head
TEST_DATABASE_URL='postgresql+psycopg://nasiya:dev_pass@127.0.0.1:5432/nasiya_test' \
  uv run alembic current
uv run ruff check .
TEST_DATABASE_URL='postgresql+psycopg://nasiya:dev_pass@127.0.0.1:5432/nasiya_test' \
  uv run pytest -ra
git diff --check
```

Docker smoke:

```bash
docker compose build web
docker compose up -d
docker compose ps
docker compose logs -f web
```

## Validation (Windows PowerShell)

```powershell
cd C:\path\to\nasiya
uv sync --dev --frozen
docker compose config --quiet
$env:TEST_DATABASE_URL = "postgresql+psycopg://nasiya:dev_pass@127.0.0.1:5432/nasiya_test"
uv run alembic upgrade head
uv run alembic current
uv run ruff check .
uv run pytest -ra
git diff --check
```

Docker smoke:

```powershell
docker compose build web
docker compose up -d
docker compose ps
docker compose logs -f web
```

`pytest -ra` skip/failure sabablarini ko'rsatadi. CI yoki local validationda
skipped testlarni yashiradigan flag ishlatilmaydi. Generic full suite real
PostgreSQL test database orqali customer migration testlarini ham avtomatik
bajaradi.

## Stop Services

Containerlarni to'xtatish uchun:

```bash
docker compose down
```

PowerShellda ham shu buyruq ishlatiladi:

```powershell
docker compose down
```

Bu container va networkni to'xtatadi, lekin PostgreSQL ma'lumotlari named
volume ichida saqlanib qoladi.

XAVFLI: containerlar bilan birga PostgreSQL ma'lumotlarini ham o'chirish:

```bash
docker compose down -v
```

`-v` named volume'ni ham o'chiradi. Bu local development va test database
ichidagi ma'lumotlarni, jumladan local user va M3 customer draftlarni yo'q
qiladi. Shuningdek `dropdb`, `DROP DATABASE`, `TRUNCATE` yoki test cleanup
buyruqlarini development database `nasiya`ga yubormang.

## PostgreSQL Dump Import

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
