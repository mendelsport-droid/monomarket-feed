# Proextreem → monoмаркет feed

Make.com раз в 30 минут скачивает Hotline-фид сайта и записывает его сюда как `data/hotline.xml`.
GitHub Actions при каждом обновлении пересобирает:

- `docs/products.xml` — товарный фид в формате monoмаркет
- `docs/prices.json` — прайс-лист (цены, наличие, гарантия, рассрочка)

Обогащение (фото, EAN, vendor code, размер, цвет, характеристики) — из `data/products.json`.
Какие товары попадают в фид — определяется галочкой **Hotline Feed** в Horoshop.
