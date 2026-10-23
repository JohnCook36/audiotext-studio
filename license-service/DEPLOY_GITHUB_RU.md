# Подключение Cloudflare к GitHub Actions для AudioText Studio

Автоматический deploy готов, но отключён до завершения настройки. Рабочий Windows-клиент пока не требует активации.

## 1. Cloudflare: создать базу D1

В панели Cloudflare откройте Storage & databases → D1 SQL Database → Create database.
Имя: audiotext-studio-licenses.
Выберите регион хранения по необходимости, затем создайте базу.
Скопируйте Database ID (UUID).

## 2. Cloudflare: получить Account ID

В Cloudflare нажмите Ctrl+K, найдите Copy account ID и нажмите этот пункт.
Account ID не является API-токеном.

## 3. Cloudflare: создать отдельный API Token

Cloudflare → Manage account → Account API Tokens → Create Token.
Для первого развёртывания выдайте доступ на создание Workers (Workers Admin / Workers Scripts Edit,
в зависимости от интерфейса аккаунта) и на редактирование D1 (D1 Edit).
Ограничьте ресурс ТОЛЬКО своим Cloudflare-аккаунтом. Скопируйте токен ОДИН РАЗ.
После первого deploy желательно заменить токен на ограниченный правами редактирования
только конкретного Worker и выбранной базы.

## 4. GitHub: положить идентификаторы в Variables

Репозиторий: https://github.com/JohnCook36/audiotext-studio
Settings → Secrets and variables → Actions → Variables → New repository variable.

CLOUDFLARE_ACCOUNT_ID = значение из шага 2
CLOUDFLARE_D1_DATABASE_ID = Database ID из шага 1
LICENSING_DEPLOY_ENABLED = false (позднее сменим на true)

## 5. GitHub: положить секретный API-токен

Settings → Secrets and variables → Actions → Secrets → New repository secret.
CLOUDFLARE_API_TOKEN = значение из шага 3.
Никогда не вставляйте токен в исходники, README, issue, скриншоты и переписку.

## 6. Генерация 3 лицензионных ключей локально

Скачайте репозиторий, откройте PowerShell в license-service и выполните:

    py -3.12 -m pip install cryptography
    py -3.12 scripts/generate_secrets.py

Файлы в license-service/keys/:
private_key.pkcs8.b64
public_key.raw.b64
admin_token.txt

Создайте в GitHub Actions Secrets три записи:

ATS_SIGNING_PRIVATE_KEY = содержимое private_key.pkcs8.b64
ATS_SIGNING_PUBLIC_KEY = содержимое public_key.raw.b64
ATS_ADMIN_TOKEN = содержимое admin_token.txt

ВАЖНО: сохраните приватный ключ и токен администратора также в защищённом локальном хранилище.
Повторная генерация приватного ключа приведёт к недействительности старых подписей.
Папка keys/ исключена из Git, не размещайте её в облачных публичных папках.

## 7. Включить и запустить

Только когда 2 Variables и 4 Secrets заполнены,
смените LICENSING_DEPLOY_ENABLED с false на true.
Откройте GitHub → Actions → Deploy AudioText License API → Run workflow.
Выберите branch main, запустите.
При успехе появится Worker audiotext-studio-licenses и будет применена миграция D1.

Дальнейшие изменения файлов license-service/** в ветке main смогут развёртываться автоматически.
Worker URL виден в Cloudflare → Compute → Workers & Pages → audiotext-studio-licenses.
Проверьте в браузере URL вида https://...workers.dev/health (ответ {"ok":true}).

## 8. Сгенерировать код активации

На своём ПК в каталоге license-service:

    py -3.12 scripts/admin.py https://ВАШ-WORKER.workers.dev issue 5 permanent

Коды выдаются один раз. Они не сохраняются открытым текстом в базе D1.
admin.py использует локальный ключ admin_token.txt; не отправляйте его никому.

## Границы готовности

Сервер развернётся автоматически после настройки GitHub.
Но принудительная активация Windows-приложения пока не включена:
ещё необходимы клиентская проверка подписи Ed25519 и интерфейс активации.
Это сделано намеренно, чтобы не заблокировать существующие установки.
