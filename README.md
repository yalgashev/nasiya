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
- `TELEGRAM_LINK_RATE_LIMIT_WINDOW_SECONDS`,
  `TELEGRAM_LINK_RATE_LIMIT_USER_ATTEMPTS`,
  `TELEGRAM_LINK_RATE_LIMIT_PHONE_ATTEMPTS`,
  `TELEGRAM_LINK_RATE_LIMIT_IP_ATTEMPTS` - M4 Telegram link token issuance
  rate-limit sozlamalari; default policy 900 soniyada 3 user, 3 phone va
  20 IP attempt.
- `RATE_LIMIT_HMAC_KEY` - raw phone/IP ni DBga yozmaslik uchun HMAC secret.
- `TELEGRAM_BOT_USERNAME` - web startup uchun optional, linking flow va worker
  preflight uchun required. Qiymat `@`siz, URLsiz, `bot` suffixli username
  bo'lishi va Bot API `getMe` natijasiga aynan mos kelishi kerak.
- `TELEGRAM_BOT_TOKEN` - faqat `telegram-worker` runtime secret. Web va CI
  uchun required emas; worker unset/empty qiymatda fail-closed ishlaydi.

Real Telegram Bot API credentialini tracked `.env`, README, CI, test fixture,
command argument yoki chatga qo'shmang.

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
docker compose build migrate web telegram-worker
docker compose up -d db migrate web
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
docker compose build migrate web telegram-worker
docker compose up -d db migrate web
Start-Process http://localhost:8000/
Start-Process http://localhost:8000/auth/login
```

Local test DB URLni alohida bering:

```powershell
$env:TEST_DATABASE_URL = "postgresql+psycopg://nasiya:dev_pass@127.0.0.1:5432/nasiya_test"
```

## Migrations

Compose development database uchun migration bir martalik `migrate` service
tomonidan DB healthdan keyin bajariladi:

```bash
docker compose up db migrate
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

M6 checkpointida `alembic current` natijasi `d4e5f6a7b8c9 (head)` bo'lishi
kerak; uning exact parent revisioni `a6b4c2d8e9f1`. Migrationni development
databasega Compose one-shot service orqali, test databasega esa faqat `_test`
bilan tugaydigan alohida `TEST_DATABASE_URL` orqali qo'llang.

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

## Shop Development Flow (M5)

Shop yaratishdan oldin owner telefoni bilan local user mavjud bo'lishi kerak.
Development buyruqlari production muhitida fail-closed ishlaydi:

```bash
docker compose exec web python -m app.cli shop create \
  --name "Demo Shop" \
  --phone +998901234567 \
  --address "Toshkent" \
  --owner-phone +998901234567
docker compose exec web python -m app.cli shop suspend <shop_uuid> \
  --reason "Development smoke"
docker compose exec web python -m app.cli shop reactivate <shop_uuid> \
  --reason "Development smoke complete"
docker compose exec web python -m app.cli demo seed
```

Login qilingandan keyin `/shop/select` active membershiplar orasidan do'kon
tanlaydi. Switcher faqat membership bittadan ko'p bo'lsa ko'rinadi. `/shop`
joriy workspace, `/shop/staff` esa active xodimlar ro'yxati va owner uchun
staff add/role/revoke oqimlarini beradi. Suspended shop read-only qoladi,
lekin `/shop/select` orqali boshqa shopga o'tish bloklanmaydi.

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

## Secure Telegram Linking Domain Foundation (M4)

M4 closeout holatida bu faqat Telegram linking domenining server-side
foundation qatlami edi. U hali end-to-end Telegram integratsiya emas edi:
real Bot API, production route/UI, webhook, worker va QR hali yo'q edi. OTP va
customer activation ham hali yo'q edi.

M4 Alembic migrationi `4f9c2d7a1b03` uchta jadval yaratadi:

- `telegram_links` - user va active yoki unlinked Telegram chat state.
- `telegram_link_tokens` - one-time link/relink token lifecycle; raw token
  saqlanmaydi, faqat lowercase SHA-256 hash saqlanadi.
- `telegram_link_events` - faqat `linked`, `unlinked`, `relinked` lifecycle
  eventlari; chat ID, token, phone, IP yoki update payload saqlanmaydi.

Token TTL qat'iy 600 soniya. Issuance rate-limit existing PostgreSQL/HMAC
limiter orqali 900 soniyada 3 user, 3 phone va 20 IP attempt siyosatini
qo'llaydi. Consume oqimi faqat typed verified-private chat identityni qabul
qiladi; M4 testlarida zero-network fake inbound boundary ishlatiladi.

M4 baseline'da `TELEGRAM_BOT_TOKEN` mavjud emas va talab qilinmaydi.
Real Telegram credential yoki network talab qilinmaydi. Quyidagi M6 bo'limi
aynan shu tarixiy baseline ustiga qo'shilgan production integrationni
tavsiflaydi.

## Production Telegram Account Linking (M6)

M6 M4 domen foundationi ustiga authenticated account UI, Telegram Bot API
adapteri, persisted long-poll cursor, poison-update quarantine, local QR va
password-protected unlink/relink oqimlarini qo'shadi. Public registration,
OTP, customer activation va webhook scope'ga kirmaydi.

`/auth/telegram` authenticated account-scoped route bo'lib, shop selection,
`active_shop_id`, membership va shop statusga bog'liq emas. Raw deep-link faqat
HTMX POST javobida bir marta `no-store` fragment sifatida ochiladi. QR shu
exact linkdan serverda in-memory PNG sifatida yaratiladi.

Worker `python -m app.telegram.worker run` bilan ishga tushadi. U bitta
PostgreSQL advisory lock, 25 soniyalik long poll, 10 soniyalik heartbeat va
TX-A/TX-B caller-owned transaction protokolidan foydalanadi. Attempt 5dagi
unknown TX-A failure quarantine qilinib cursor bilan atomik commit qilinadi.
CI va automated testlar injected fake transport ishlatadi va real Telegram
network yoki credential talab qilmaydi.

Minimal local operation:

```bash
docker compose up -d db migrate web
docker compose up -d telegram-worker
docker compose exec telegram-worker python -m app.telegram.worker healthcheck
```

Worker uchun real dev/test secret untracked runtime konfiguratsiyada bo'lishi
kerak. Secret yo'q muhitda faqat webni ishga tushiring; workerning fail-closed
chiqishi expected. Batafsil deployment, rotation va incident amallari
`docs/m6_worker_runbook.md`da.

## Telegram Login OTP (M7)

M7 mavjud active user va uning active Telegram linki uchun optional
`LOGIN` OTP oqimini qo'shadi. Password login saqlanadi va dispatcher yoki
Telegram vaqtincha unavailable bo'lsa ham ishlaydi. Registration, activation,
recovery, password reset, phone change, SMS va generic notification/outbox
M7 scope'iga kirmaydi.

Web OTP challenge va durable dispatch intentni PostgreSQLda yaratadi, lekin
Telegram networkni chaqirmaydi. Alohida dispatcher pending ishni yuboradi:

```bash
docker compose --env-file .env.local up -d db migrate web
docker compose --env-file .env.local up -d telegram-worker otp-dispatcher
docker compose --env-file .env.local exec otp-dispatcher \
  python -m app.otp.dispatcher healthcheck
```

Expected migration head `e7f8a9b0c1d2`. Real dev/test acceptance
credentiallari faqat ignored, mode `600` bo'lgan `.env.local` kabi untracked
runtime manbasida saqlanadi. Uning mazmunini hech qachon terminalga, logga,
reportga yoki CIga chiqarmang va tracked qilmang. Webga bot token berilmaydi;
OTP-enabled web va dispatcher bir xil dedicated `OTP_HMAC_KEY`dan foydalanadi.

Automated dev/test real credential yoki Telegram network ishlatmaydi:

```bash
uv run pytest -q \
  tests/test_otp_dispatcher.py \
  tests/test_otp_concurrency_containment_matrix.py \
  tests/test_otp_enumeration_matrix.py \
  tests/test_otp_sensitive_data_audit.py
```

Fake transport real network/device acceptance o'rnini bosmaydi. Dispatcher
deployment, health, `UNKNOWN` holati, rotation va sanitized acceptance
amallari `docs/m7_dispatcher_runbook.md`da.

## Secure Object Storage Foundation (M8)

M8 backend-mediated JPEG, PNG va WebP sanitization, private S3-compatible
storage, `object_files` lifecycle va internal operator CLI foundationini
qo'shadi. Bu generic media vault emas: production upload/download/delete
route, public file route va domain consumer mavjud emas.

Local private MinIO uchun credentiallarni faqat untracked secure environment
orqali bering. Root/admin credential faqat `minio` va `minio-init`ga,
bucket-scoped app credential esa faqat web/storage CLIga beriladi. Hech qanday
credential, endpoint, bucket yoki object qiymatini README, command argument,
log yoki reportga ko'chirmang.

Minimal local provisioning:

```bash
docker compose up -d db minio
docker compose run --rm minio-init
docker compose run --rm minio-init
docker compose up -d migrate web
```

App identity bilan ishlaydigan safe operator buyruqlari:

```bash
docker compose exec web python -m app.cli storage preflight
docker compose exec web python -m app.cli storage smoke \
  --actor-id "$DEV_ACTOR_ID"
docker compose exec web python -m app.cli storage reconcile \
  --batch-size 100
docker compose exec web python -m app.cli storage delete \
  --object-id "$DEV_OBJECT_ID"
```

`storage delete` development/testing-only internal operatsiya. Preflight faqat
configured storage data-plane accessni tekshiradi; private policy va anonymous
denial `minio-init`, CI provisioning va designated real-MinIO acceptance bilan
isbotlanadi.

Storage web startup uchun optional. `/health` green bo'lishi storage tayyor
deganini anglatmaydi; storage-specific preflight authoritative. Storage
unavailable bo'lsa capability fail-closed ishlaydi va local-disk fallback
yo'q. M6 Telegram worker va M7 OTP dispatcher storage/root credential hamda
MinIO dependency olmaydi.

`deploy/minio-backup-restore-exercise.sh` faqat synthetic temporary private
storage bilan local mashq qiladi. U production backup, recovery yoki RPO/RTO
dalili emas; uni faqat `docs/m8_storage_runbook.md` bo'yicha bajaring.
`docker compose down -v` PostgreSQL va MinIO named volumelarini o'chiradigan
destructive buyruq, shuning uchun normal operation, acceptance yoki backup
mashqida uni ishlatmang.

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
uv run ruff format --check .
TEST_DATABASE_URL='postgresql+psycopg://nasiya:dev_pass@127.0.0.1:5432/nasiya_test' \
  uv run pytest -ra
git diff --check
```

Docker smoke:

```bash
docker compose build --no-cache migrate web telegram-worker
docker compose up -d db migrate web
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
uv run ruff format --check .
uv run pytest -ra
git diff --check
```

Docker smoke:

```powershell
docker compose build --no-cache migrate web telegram-worker
docker compose up -d db migrate web
docker compose ps
docker compose logs -f web
```

`pytest -ra` skip/failure sabablarini ko'rsatadi. CI yoki local validationda
skipped testlarni yashiradigan flag ishlatilmaydi. Generic full suite real
PostgreSQL test database orqali customer migration testlari bilan birga M4
Telegram migration/replay/concurrency/retention va M5 shop
persistence/tenant/lifecycle/HTTP containment testlarini, shuningdek M6 Bot
API fake transport, polling persistence, worker recovery, QR va account web
flow hamda M7 OTP dispatcher, containment, security va web flow testlarini ham
avtomatik bajaradi. Real Telegram credential yoki network talab qilinmaydi.

## Stop Services

Containerlarni to'xtatish uchun:

```bash
docker compose down
```

PowerShellda ham shu buyruq ishlatiladi:

```powershell
docker compose down
```

Bu container va networkni to'xtatadi, lekin PostgreSQL ma'lumotlari va private
MinIO objectlari o'z named volumelari ichida saqlanib qoladi.

XAVFLI: containerlar bilan birga PostgreSQL ma'lumotlarini ham o'chirish:

```bash
docker compose down -v
```

`-v` named volume'ni ham o'chiradi. Bu local development va test database
ichidagi ma'lumotlarni, jumladan local user, M3 customer draftlar va M4
Telegram linking test/local state'ni, shuningdek private MinIO objectlarini
yo'q qiladi. Shuningdek `dropdb`, `DROP DATABASE`, `TRUNCATE` yoki test cleanup
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
