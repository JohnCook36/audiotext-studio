# AudioText Studio — Cloudflare License Service

Статус: подготовительная отдельная ветка. Основная AudioText Studio 1.3.0 пока не требует лицензии.

## С чего начать

1. Создайте аккаунт Cloudflare на https://dash.cloudflare.com/sign-up, подтвердите почту.
2. Установите Node.js LTS и Python 3.12+ на свой компьютер.
3. Скачайте эту ветку репозитория GitHub и откройте PowerShell в каталоге license-service.
4. Запустите команды:

    npm install
    npx wrangler login
    npx wrangler d1 create audiotext-studio-licenses

5. Скопируйте выданный UUID базы данных и замените строку REPLACE_WITH_D1_DATABASE_UUID в wrangler.toml.
6. На своём ПК создайте ключи и секрет администратора:

    py -3.12 -m pip install cryptography
    py -3.12 scripts/generate_secrets.py

7. Создайте таблицы и опубликуйте Worker:

    npx wrangler d1 migrations apply audiotext-studio-licenses --remote
    npx wrangler deploy

8. В Cloudflare Worker добавьте три секрета (выполните npx wrangler secret put ИМЯ, затем вставьте содержимое файла):
   ATS_SIGNING_PRIVATE_KEY из keys/private_key.pkcs8.b64
   ATS_SIGNING_PUBLIC_KEY из keys/public_key.raw.b64
   ATS_ADMIN_TOKEN из keys/admin_token.txt

9. Проверьте https://ВАШ_WORKER.workers.dev/health и создайте коды:

    py -3.12 scripts/admin.py https://ВАШ_WORKER.workers.dev issue 5 permanent
    py -3.12 scripts/admin.py https://ВАШ_WORKER.workers.dev list

Для ограниченных лицензий укажите число дней вместо permanent, например 30.
Сброс привязки: admin.py URL reset UUID. Блокировка: admin.py URL revoke UUID.

## API и безопасность

POST /v1/activate принимает одноразовый код и SHA-256 отпечаток ПК.
POST /v1/renew получает подписанную лицензию и обновляет срок локальной проверки.
GET /v1/admin/licenses возвращает список лицензий, без секретных кодов.
POST /v1/admin/issue создаёт 1–50 случайных кодов, видимых только в ответе.
POST /v1/admin/revoke блокирует лицензию, POST /v1/admin/reset отвязывает ПК.

Привязка к одному устройству производится атомарно, повторная активация того же кода на другом компьютере отклоняется. Подписанная лицензия действует офлайн до 90 дней. Отзыв офлайн-лицензии не действует до истечения подписанного срока.

Закрытый ключ и токен администратора не попадают в GitHub: каталог keys игнорируется Git. Лицензионный сервер не получает видео, аудио или расшифрованные тексты. В базу записывается только SHA-256 кодов и непрозрачный идентификатор устройства.

До публичного запуска нужно реализовать проверку подписей в Windows-клиенте и экран активации, зафиксировать публичный ключ в сборке, провести интеграционные испытания на двух ПК, настроить Cloudflare WAF/Rate Limiting и, по возможности, Cloudflare Access для административных маршрутов.

ВАЖНО: пока Cloudflare не настроен, мы не активируем принудительную блокировку Windows-приложения, чтобы не лишить доступа существующих пользователей. Эта ветка не публикует установщик.
