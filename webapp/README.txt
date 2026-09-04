BALTICAR Mini App — исправление v1

Что исправлено:
1) Стартовый экран бота теперь минимальный: фото BALTICAR + короткое описание + одна кнопка «🚗 Открыть BALTICAR Mini App».
2) Отзывы, условия, контакты, «Почему BALTICAR» и «Мои брони» находятся внутри Mini App.
3) Mini App отдаётся по абсолютному пути относительно bot.py, а папка photos — по абсолютному пути. Это устраняет пустой экран при другом рабочем каталоге Render.
4) В интерфейсе Mini App бренд везде BALTICAR.

Структура в GitHub:
Calendar-v2/
  bot.py
  webapp/
    index.html
  photos/
    balticar_hero.jpg
    solaris21_hero.jpg
    solaris20_hero.jpg
    solaris17_hero.jpg
    i30_hero.jpg

Фотографии повторно загружать не нужно, если они уже есть в photos.
