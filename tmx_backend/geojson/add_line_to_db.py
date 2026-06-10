#!/usr/bin/env python3
import json
import re
import argparse
import psycopg2


def normalize_hex(value: str) -> str:
    value = value.strip().lstrip("#")

    if not re.fullmatch(r"[0-9a-fA-F]{6}", value):
        raise ValueError("hex должен быть ровно 6 hex-символов, например 78C7C9 или #78C7C9")

    return value.upper()


def build_multilinestring(geojson_path: str) -> dict:
    with open(geojson_path, "r", encoding="utf-8") as f:
        gj = json.load(f)

    features = gj.get("features")

    if not isinstance(features, list):
        raise ValueError(
            "Это не GeoJSON FeatureCollection. "
            "Нужен файл с полем features. "
            "Из Overpass Turbo экспортируй именно GeoJSON, а не обычный JSON."
        )

    line_parts = []

    for feat in features:
        geom = feat.get("geometry") or {}
        gtype = geom.get("type")
        coords = geom.get("coordinates")

        if not coords:
            continue

        if gtype == "LineString":
            if len(coords) >= 2:
                line_parts.append(coords)

        elif gtype == "MultiLineString":
            for part in coords:
                if part and len(part) >= 2:
                    line_parts.append(part)

    if not line_parts:
        raise ValueError(
            "В файле не найдено LineString/MultiLineString. "
            "Скорее всего, ты выгрузил только relation/center без геометрии путей."
        )

    return {
        "type": "MultiLineString",
        "coordinates": line_parts
    }


def main():
    parser = argparse.ArgumentParser(
        description="Загрузка геометрии ветки метро в таблицу lines"
    )

    parser.add_argument("--geojson", required=True, help="Путь к GeoJSON-файлу ветки метро")
    parser.add_argument("--dsn", required=True, help="PostgreSQL DSN")
    parser.add_argument("--name", required=True, help="Название линии")
    parser.add_argument("--number", required=True, help="Номер линии, например 1, 4А, 8А, 11")
    parser.add_argument("--hex", required=True, help="Цвет линии, например #78C7C9 или 78C7C9")

    args = parser.parse_args()

    line_geom = build_multilinestring(args.geojson)
    line_geom_json = json.dumps(line_geom, ensure_ascii=False)
    line_hex = normalize_hex(args.hex)

    sql = """
        INSERT INTO lines(name, number, hex, geom)
        VALUES (
            %s,
            %s,
            %s,
            ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)
        )
        ON CONFLICT (number)
        DO UPDATE SET
            name = EXCLUDED.name,
            hex = EXCLUDED.hex,
            geom = EXCLUDED.geom
        RETURNING id_line, name, number, hex;
    """

    with psycopg2.connect(args.dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS postgis;")
            cur.execute(sql, (args.name, args.number, line_hex, line_geom_json))
            row = cur.fetchone()

        conn.commit()

    print("OK: линия записана в БД")
    print(f"id_line={row[0]}, name={row[1]}, number={row[2]}, hex={row[3]}")
    print(f"Сегментов в MultiLineString: {len(line_geom['coordinates'])}")


if __name__ == "__main__":
    main()
    
    
    
# #!/usr/bin/env python3
# import json
# import re

# import argparse
# import psycopg2

# def build_multilinestring(geojson_path: str) -> dict:
#     with open(geojson_path, "r", encoding="utf-8") as f:
#         gj = json.load(f)

#     features = gj.get("features", [])
#     lines = []

#     for feat in features:
#         geom = (feat.get("geometry") or {})
#         gtype = geom.get("type")
#         coords = geom.get("coordinates")

#         if not coords:
#             continue

#         if gtype == "LineString":
#             # coords: [[lon,lat], [lon,lat], ...]
#             lines.append(coords)
#         elif gtype == "MultiLineString":
#             # coords: [ LineString1, LineString2, ... ]
#             lines.extend(coords)
#         else:
#             # Point/Polygon и т.п. игнорируем
#             continue

#     if not lines:
#         raise ValueError("В GeoJSON не найдено ни одной LineString/MultiLineString геометрии.")

#     return {"type": "MultiLineString", "coordinates": lines}


# def main():
#     ap = argparse.ArgumentParser(description="Load Overpass GeoJSON tracks into PostGIS line table as MULTILINESTRING.")
#     ap.add_argument("--geojson", required=True, help="Path to exported .geojson from Overpass Turbo")
#     ap.add_argument("--dsn", required=True, help='Postgres DSN, e.g. "host=109.254.86.44 dbname=tmx user=postgres password=JHVljvJG^%9f7V(^vOVlvkg59+4 port=5000"')

#     ap.add_argument("--name", default="Большая кольцевая", help="Line name")
#     ap.add_argument("--number", default=11, help="Line number")
#     ap.add_argument("--hex", default="82C0C0", help="6-hex color without #, e.g. 82C0C0")

#     args = ap.parse_args()

#     if not re.fullmatch(r"[0-9a-fA-F]{6}", args.hex or ""):
#         raise ValueError("hex должен быть ровно 6 hex-символов, например 82C0C0")

#     ml = build_multilinestring(args.geojson)
#     ml_json = json.dumps(ml, ensure_ascii=False)

#     #sql_delete = "DELETE FROM line WHERE number = %s;"
#     sql_insert = """
#         INSERT INTO line(name, number, hex, geom)
#         VALUES (%s, %s, %s, ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326));
#     """

#     with psycopg2.connect(args.dsn) as conn:
#         with conn.cursor() as cur:
#             # на всякий случай: PostGIS
#             cur.execute("CREATE EXTENSION IF NOT EXISTS postgis;")

#             # перезапись линии по номеру
#             #cur.execute(sql_delete, (args.number,))
#             cur.execute(sql_insert, (args.name, args.number, args.hex.upper(), ml_json))

#         conn.commit()

#     print(f"OK: inserted line '{args.name}' number={args.number} hex={args.hex.upper()}")
#     print(f"Segments in MultiLineString: {len(ml['coordinates'])}")


# if __name__ == "__main__":
#     main()