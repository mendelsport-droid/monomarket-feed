#!/usr/bin/env python3
"""Proextreem -> Monomarket feed builder.

Источник: Hotline-фид Horoshop (какие товары, цена, старая цена, наличие)
Обогащение: data/products.json (фото, штрихкод, vendor code, размер, цвет, характеристики, описание)
Результат: docs/products.xml (формат monoмаркет) + docs/prices.json (прайс-лист monoмаркет)

Запуск: python3 convert.py            (качает фид)
        python3 convert.py feed.xml   (из локального файла)
"""
import html, json, re, sys, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

FEED_URL = "https://proextreem.com.ua/ua/marketplace-integration/hotline?langId=2"
ROOT = Path(__file__).parent
DATA = ROOT / "data" / "products.json"
OUT = ROOT / "docs"

CFG = {
    "warehouse_id": "DNIPRO-MAIN",
    "stock_if_available": 1,          # Horoshop не отдаёт количество
    "days_to_dispatch": 3,
    "warranty_type": "manufacturer",
    "min_year": 2025,                 # товары старше (по полю «Год») в monoмаркет не отдаём
    "exclude_codes": ["700285659428"],   # ручные исключения (артикул или код из Hotline-фида)
    "pay_in_parts": 7,                # везде 7 ПЧ (минимум monoмаркет)
    "category_map": {                 # раздел Horoshop -> категория monoмаркет
        "Лыжные ботинки": "Гірськолижні черевики",
        "Крепления для сноуборда": "Кріплення для сноуборда",
        "Шлемы защитные": "Гірськолижні шоломи",
        "Очки-маски": "Гірськолижні маски",
    },
    "type_prefix": {                  # как начинать название
        "Лыжные ботинки": "Черевики гірськолижні",
        "Крепления для сноуборда": "Кріплення для сноуборда",
        "Шлемы защитные": "Шолом гірськолижний",
        "Очки-маски": "Маска гірськолижна",
    },
    "dimensions": {                   # ВГХ упаковки по разделу: кг, см (В×Ш×Д)
        "Лыжные ботинки": (2.5, 18, 32, 42),
        "Крепления для сноуборда": (1.6, 14, 30, 38),
        "Шлемы защитные": (0.6, 25, 25, 30),
        "Очки-маски": (0.3, 12, 12, 22),
    },
}
EAN = re.compile(r"^\d{8}$|^\d{12,14}$")
COLOR_UA = {"черный": "Чорний", "белый": "Білий", "серый": "Сірий", "синий": "Синій", "красный": "Червоний",
            "зеленый": "Зелений", "желтый": "Жовтий", "оранжевый": "Помаранчевий", "розовый": "Рожевий",
            "фиолетовый": "Фіолетовий", "коричневый": "Коричневий", "голубой": "Блакитний", "бежевый": "Бежевий"}


def color_ua(c):
    return COLOR_UA.get(c.strip().lower(), c.strip().capitalize() if c.islower() or c.isupper() else c.strip())


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; ProextreemFeed/1.0)"})
    with urllib.request.urlopen(req, timeout=60) as f:
        return f.read()


def txt(el, tag):
    x = el.find(tag)
    return (x.text or "").strip() if x is not None and x.text else ""


def model_name(name, brand):
    t = re.sub(r"\(.*?\)", "", name)
    t = re.sub(r"^(Черевики гірськолижні|Кріплення для сноуборда|Кріплення|Черевики|Гірськолижний шолом|Гірськолижна маска|Шолом|Маска)\s+", "", t).strip()
    t = re.sub(r"^" + re.escape(brand) + r"\s+", "", t, flags=re.I).strip()
    t = re.sub(r"\s+20\d\d$", "", t)
    return re.sub(r"\s+", " ", t)


def title(st, cfg):
    brand = st["brand"].capitalize() if st["brand"].isupper() else st["brand"]
    parts = [cfg["type_prefix"].get(st["section"], ""), brand, model_name(st["name"], st["brand"]), st["year"],
             color_ua(st["color"]) if st["color"] else "", st["size"]]
    t = " ".join(p for p in parts if p)
    if st["vendor_code"]:
        t += f" ({st['vendor_code']})"
    return re.sub(r"\s+", " ", t)[:100]


def clean_desc(d):
    d = html.unescape(d)
    d = re.sub(r"<h[1-4]([^>]*)>", r"<h5\1>", d)
    d = re.sub(r"</h[1-4]>", "</h5>", d)
    return d.replace("\r", "").strip()


def main():
    cfg = CFG
    static = json.loads(DATA.read_text(encoding="utf-8"))
    # Horoshop в Hotline-фиде отдаёт <code> = код производителя (MPN), если он заполнен, иначе артикул
    by_mpn, by_photo = {}, {}
    for k, v in static.items():
        if v.get("vendor_code"):
            by_mpn.setdefault(v["vendor_code"].strip(), k)
        for ph in v.get("photos", []):
            by_photo.setdefault(ph, v)
    raw = Path(sys.argv[1]).read_bytes() if len(sys.argv) > 1 else fetch(FEED_URL)
    root = ET.fromstring(raw)
    items = root.find("items")
    offers, prices, skipped, nophoto = [], [], [], []
    # Horoshop отдаёт <code> = MPN, а MPN бывает общим для нескольких размеров -> берём штрихкод
    from collections import Counter
    code_count = Counter(txt(it, "code") for it in items)
    for it in items:
        code = txt(it, "code")
        st = static.get(code) or static.get(by_mpn.get(code.strip(), ""))
        if code_count[code] > 1:
            # несколько модификаций с одним MPN (размеры): различаем по фото модификации из фида
            st = by_photo.get(txt(it, "image")) or (st if st and st.get("vendor_code", "").strip() != code.strip() else None)
        if st and code_count[code] > 1 and st.get("barcode"):
            code = st["barcode"]
        available = txt(it, "stock").lower().startswith("в нали")
        price = int(float(txt(it, "priceRUAH") or 0))
        old = int(float(txt(it, "oldprice") or 0))
        if not st:
            # товар есть в фиде, но нет в data/products.json — отдаём как есть, с 1 фото
            name = html.unescape(txt(it, "name"))
            st = {"name": name, "brand": txt(it, "vendor"), "section": "", "barcode": code if EAN.match(code) else "",
                  "vendor_code": (re.search(r"\(\s*([A-Za-z0-9][A-Za-z0-9.\-/]+)\s*\)", name) or [None, ""])[1],
                  "size": "", "color": "", "year": (re.search(r"\b(20\d\d)\b", name) or [None, ""])[1],
                  "photos": [txt(it, "image")], "desc": txt(it, "description"), "params": {},
                  "warranty": int(txt(it, "guarantee") or 12), "parts": 6}
            skipped.append(code)
        if not available or not price:
            continue
        if code in cfg["exclude_codes"] or st["barcode"] in cfg["exclude_codes"]:
            continue
        y = re.search(r"\d{4}", st["year"] or "")
        if y and int(y.group()) < cfg["min_year"]:
            continue
        # vendor_code должен быть уникальным на каждый SKU: код производителя + размер
        vc_unique = st["vendor_code"] or code
        if st["vendor_code"]:
            col = re.sub(r"[^A-Za-z0-9]+", "", st["color"]).upper()[:6]   # только латиница (кириллицу не пишем)
            suffix = "-".join(x for x in (col, st["size"]) if x)
            vc_unique += f"-{suffix}" if suffix else f"-{code}"
        # нет фото у модификации — берём у соседней (та же модель и цвет)
        if not st["photos"]:
            for s2 in static.values():
                if s2["photos"] and s2["name"] == st["name"] and s2["color"] == st["color"] and s2["vendor_code"] == st["vendor_code"]:
                    st["photos"] = s2["photos"]
                    break
        if not st["photos"]:
            nophoto.append(code)          # monoмаркет без фото не примет — не отдаём
            continue
        cat = cfg["category_map"].get(st["section"], st["section"] or "Інше")
        kg, h, w, l = cfg["dimensions"].get(st["section"], (1.0, 10, 30, 40))
        params = [("Бренд", st["brand"]), ("Модель", model_name(st["name"], st["brand"]))]
        params += [(k, color_ua(v) if k == "Колір" else v) for k, vs in st["params"].items() for v in vs]
        o = ["  <offer>",
             f"   <id>{escape(code)}</id>",
             f"   <code>{escape(code)}</code>",
             f"   <vendor_code>{escape(vc_unique)}</vendor_code>",
             f"   <title><![CDATA[{title(st, cfg)}]]></title>"]
        if st["barcode"]:
            o.append(f"   <barcode>{st['barcode']}</barcode>")
        o += [f"   <category>{escape(cat)}</category>",
              f"   <brand>{escape(st['brand'])}</brand>",
              "   <availability>Є в наявності</availability>",
              f"   <weight>{kg}</weight>", f"   <height>{h}</height>", f"   <width>{w}</width>", f"   <length>{l}</length>",
              f"   <description><![CDATA[{clean_desc(st['desc'])}]]></description>",
              "   <image_link>" + "".join(f"<picture>{escape(p)}</picture>" for p in st["photos"]) + "</image_link>",
              "   <tags>" + "".join(f'<param name="{escape(k, {chr(34): "&quot;"})}">{escape(str(v))}</param>' for k, v in params if v) + "</tags>",
              "  </offer>"]
        offers.append("\n".join(o))
        prices.append({"code": code, "price": price, "old_price": old if old > price else None, "availability": True,
                       "stock": cfg["stock_if_available"],
                       "warehouses": [{"id": cfg["warehouse_id"], "stock": cfg["stock_if_available"]}],
                       "warranty_type": cfg["warranty_type"], "warranty_period": st["warranty"],
                       "max_pay_in_parts": cfg["pay_in_parts"], "days_to_dispatch": cfg["days_to_dispatch"], "manufacture": None})

    OUT.mkdir(exist_ok=True)
    (OUT / "products.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n<Market>\n <offers>\n' + "\n".join(offers)
                                      + "\n </offers>\n</Market>\n", encoding="utf-8")
    (OUT / "prices.json").write_text(json.dumps({"updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                                 "total": len(prices), "data": prices}, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "index.html").write_text(f"<meta charset=utf-8><p>Proextreem → monoмаркет: {len(offers)} товарів, оновлено "
                                    f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC</p><a href=products.xml>products.xml</a> · <a href=prices.json>prices.json</a>",
                                    encoding="utf-8")
    print(f"offers: {len(offers)}, prices: {len(prices)}, без обогащения: {len(skipped)}, без фото (пропущены): {nophoto}")


if __name__ == "__main__":
    main()
