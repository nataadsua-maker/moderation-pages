# moderation-pages

GitHub Actions для сервиса автомодерации связок RSOC.

- `analyzer/` — Python код анализатора (видео + NIM Llama + полиси)
- `.github/workflows/moderate.yml` — Action триггерится через `repository_dispatch` от Cloudflare Worker'а

Разбор заявки живёт в кабинете воркера (`/sub/<id>`), туда же ведёт ссылка из бота.
Страниц на GitHub Pages больше нет: шаблон и рендер удалены как мёртвый код.

Управляющий Worker и архитектура: [cpa-arbitrage/moderation-service](https://github.com/nataadsua-maker/moderation-service) (Nataliia's workspace).
