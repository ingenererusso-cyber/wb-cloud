# Vendra frontend (Tailwind, shadcn/ui, Lucide)

Сборка кладётся в `../static/frontend/`, откуда Django отдаёт файлы по `{% static 'frontend/...' %}`.

## Команды

```bash
cd frontend
npm install
npm run dev
```

Сборка для продакшена (база путей для Django static):

```bash
VITE_DJANGO_BASE=1 npm run build
```

Дополнительные компоненты shadcn (из `frontend/`):

```bash
npx shadcn@latest add card
npx shadcn@latest add input
# …
```

Иконки: `import { … } from "lucide-react"`.

## Подключение к шаблону Django

После `npm run build` в `static/frontend/index.html` появятся теги со ссылками на `assets/*.js` и `assets/*.css`. Скопируйте `link` / `script type="module"` в базовый шаблон, либо подключайте `assets/index-*.css` и `assets/index-*.js` (имена с хэшем меняются при каждой сборке).

Пока dev-only можно проксировать Vite: `python manage.py runserver` + `npm run dev` и iframe или отдельная страница на `http://127.0.0.1:5173/`.

Контекст дизайна: корневой `DESIGN.md`.
