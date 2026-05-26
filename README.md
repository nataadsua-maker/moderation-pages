# moderation-pages

GitHub Pages + GitHub Actions для сервиса автомодерации связок RSOC.

- `analyzer/` — Python код анализатора (видео + NIM Llama + полиси)
- `.github/workflows/moderate.yml` — Action триггерится через `repository_dispatch` от Cloudflare Worker'а
- `templates/` — Jinja2 шаблоны страниц разбора
- `docs/` — GitHub Pages output (`docs/sub/<id>/index.html`)

URL Pages: <https://nataadsua-maker.github.io/moderation-pages/>

Управляющий Worker и архитектура: [cpa-arbitrage/moderation-service](https://github.com/nataadsua-maker/moderation-service) (Nataliia's workspace).
