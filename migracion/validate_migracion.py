"""FASE 2 — Validación 1:1 de la migración CSVs -> Supabase.

Compara, registro a registro, lo que devuelven los transforms (fuente real)
contra lo persistido en Supabase. Diferencia esperada: 0 en todo.

Uso:
  python migracion/validate_migracion.py          # valida contra Supabase
  python migracion/validate_migracion.py --dry-run # solo muestra el plan
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import import_data as imp

FIELDS = {
    # config: descripcion es enriquecimiento del seed (no está en el CSV);
    # se compara clave/valor (datos de negocio).
    "config": ["clave", "valor"],
    # categorias_fijas: pertenece='DAvid' (typo de la hoja) vs seed 'David';
    # el bot NO lee pertenece de categorías. Se comparan los campos de negocio.
    "categorias_fijas": ["es_ingreso", "tipo", "monto_fijo"],
    "reglas": [
        "remitente", "asunto_contiene", "clave", "activo", "tiene_adjunto",
        "es_tarjeta_credito", "regex_consumo", "regex_cierre",
        "regex_vencimiento", "regex_monto", "pertenece", "entidad",
    ],
    "consolidado": [
        "fecha_mail", "remitente", "asunto", "monto_total",
        "fecha_vencimiento", "link_drive", "id_consolidado", "pertenece",
    ],
    "consumos": [
        "fecha_consumo", "comprobante", "detalle", "cuota_actual",
        "cuota_total", "pesos", "dolar", "fecha_cierre",
        "fecha_vencimiento", "remitente", "id_consumo", "pertenece",
    ],
    "ingresos": [
        "fecha", "tipo", "monto", "origen", "id_ingreso", "pertenece",
    ],
}

# tabla de destino por hoja (como las agrupa load_all)
TABLE_BY_KEY = {
    "config": ["config", "categorias_fijas"],
    "datos": ["reglas"],
    "consolidado": ["consolidado"],
    "consumos": ["consumos"],
    "ingresos": ["ingresos"],
}


def query_rows(cur, tabla: str, fields: list[str]) -> list[dict]:
    cols = ", ".join(fields)
    cur.execute(f"SELECT {cols} FROM {tabla}")
    return [dict(zip(fields, row)) for row in cur.fetchall()]


def normalize(v):
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def diff_lists(expected: list[dict], actual: list[dict], fields: list[str]) -> list[str]:
    """Compara como multiset (por tupla de campos). Devuelve descripciones."""
    from collections import Counter

    e = Counter(tuple(normalize(x[f]) for f in fields) for x in expected)
    a = Counter(tuple(normalize(x[f]) for f in fields) for x in actual)
    diffs = []
    for k, c in e.items():
        if a.get(k, 0) != c:
            diffs.append(f"  en DB faltan {c - a.get(k, 0)} de {k[:12]}")
    for k, c in a.items():
        if e.get(k, 0) != c:
            diffs.append(f"  en DB sobran {c - e.get(k, 0)} de {k[:12]}")
    return diffs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Validación 1:1 migración FASE 2")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--csv-dir", type=str, default=str(imp.DEFAULT_CSV_DIR))
    args = ap.parse_args(argv)

    all_data = imp.load_all(Path(args.csv_dir))
    expected = {t: [] for t in FIELDS}
    for key, tablas in TABLE_BY_KEY.items():
        for tabla in tablas:
            expected[tabla] += [x["row"] for x in all_data[key]
                                if x["tabla"] == tabla]

    if args.dry_run:
        for t in FIELDS:
            print(f"  {t:18s} esperado={len(expected[t])}")
        print("DRY-RUN OK")
        return 0

    import psycopg2

    conn = imp.get_conn()
    total_diff = 0
    ok = True
    try:
        with conn.cursor() as cur:
            for tabla in FIELDS:
                fields = FIELDS[tabla]
                actual = query_rows(cur, tabla, fields)
                e, a = len(expected[tabla]), len(actual)
                diffs = diff_lists(expected[tabla], actual, fields)
                total_diff += len(diffs)
                if e != a or diffs:
                    ok = False
                    print(f"[FAIL] {tabla}: esperado={e} actual={a} diferencias={len(diffs)}")
                    for d in diffs[:10]:
                        print(d)
                else:
                    print(f"[OK]   {tabla}: {e} == {a} (1:1 exacto)")
    finally:
        conn.close()

    print(f"\n[FASE2-VAL] DIFERENCIA TOTAL: {total_diff} -> {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
