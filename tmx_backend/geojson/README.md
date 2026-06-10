set +H

DSN='host=109.254.86.44 port=5555 dbname=photo user=postgres password=StrongPass123!RFhdvdcdn@@+5'

python3 add_line_to_db.py \
  --geojson ./geojson/1.geojson \
  --dsn "$DSN" \
  --name "Сокольническая линия" \
  --number "1" \
  --hex "E42313"

python3 add_line_to_db.py \
  --geojson ./geojson/2.geojson \
  --dsn "$DSN" \
  --name "Замоскворецкая линия" \
  --number "2" \
  --hex "4FB04F"

python3 add_line_to_db.py \
  --geojson ./geojson/3.geojson \
  --dsn "$DSN" \
  --name "Арбатско-Покровская линия" \
  --number "3" \
  --hex "0072BA"

python3 add_line_to_db.py \
  --geojson ./geojson/4.geojson \
  --dsn "$DSN" \
  --name "Филёвская линия" \
  --number "4" \
  --hex "1EBCEF"

python3 add_line_to_db.py \
  --geojson ./geojson/5.geojson \
  --dsn "$DSN" \
  --name "Кольцевая линия" \
  --number "5" \
  --hex "A35539"

python3 add_line_to_db.py \
  --geojson ./geojson/6.geojson \
  --dsn "$DSN" \
  --name "Калужско-Рижская линия" \
  --number "6" \
  --hex "F07E23"

python3 add_line_to_db.py \
  --geojson ./geojson/7.geojson \
  --dsn "$DSN" \
  --name "Таганско-Краснопресненская линия" \
  --number "7" \
  --hex "943E90"

python3 add_line_to_db.py \
  --geojson ./geojson/8.geojson \
  --dsn "$DSN" \
  --name "Калининская линия" \
  --number "8" \
  --hex "FFCD1C"

python3 add_line_to_db.py \
  --geojson ./geojson/8A.geojson \
  --dsn "$DSN" \
  --name "Солнцевская линия" \
  --number "8А" \
  --hex "FFCD1C"

python3 add_line_to_db.py \
  --geojson ./geojson/9.geojson \
  --dsn "$DSN" \
  --name "Серпуховско-Тимирязевская линия" \
  --number "9" \
  --hex "ADACAC"

python3 add_line_to_db.py \
  --geojson ./geojson/10.geojson \
  --dsn "$DSN" \
  --name "Люблинско-Дмитровская линия" \
  --number "10" \
  --hex "BED12C"

python3 add_line_to_db.py \
  --geojson ./geojson/11.geojson \
  --dsn "$DSN" \
  --name "Большая кольцевая линия" \
  --number "11" \
  --hex "78C7C9"

python3 add_line_to_db.py \
  --geojson ./geojson/12.geojson \
  --dsn "$DSN" \
  --name "Бутовская линия" \
  --number "12" \
  --hex "bac8e8"

python3 add_line_to_db.py \
  --geojson ./geojson/15.geojson \
  --dsn "$DSN" \
  --name "Некрасовская линия" \
  --number "15" \
  --hex "F088B6"

python3 add_line_to_db.py \
  --geojson ./geojson/16.geojson \
  --dsn "$DSN" \
  --name "Троицкая линия" \
  --number "16" \
  --hex "007763"
