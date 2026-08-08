"""FASE 2 — Migración de datos: CSVs (export Sheets) -> Supabase.

Reproducible e idempotente:
  - Lee los 5 CSVs del export de las hojas.
  - Normaliza fechas/montos/booleanos/comprobantes (preservando el dato
    original en columnas `id_*` legacy).
  - Inserta con ON CONFLICT DO NOTHING (re-ejecutar no duplica).
  - Valida y reporta conteos por entidad.

Uso:
  python migracion/import_data.py                 # carga completa
  python migracion/import_data.py --dry-run       # solo parsea y valida
  python migracion/import_data.py --csv-dir <dir> # fuente alternativa

Entorno:
  SUPABASE_DB_URL  (postgresql://...)  o  SUPABASE_DBPW (build pooler).
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import sys
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV_DIR = Path(
    r"C:\Users\dgame\OneDrive\Escritorio\APPS\RESUMENES\sheet_resumen"
)
CSV_FILES = {
    "config": "Resumenes_bot - Config.csv",
    "consolidado": "Resumenes_bot - Consolidado.csv",
    "consumos": "Resumenes_bot - Consumos.csv",
    "datos": "Resumenes_bot - Datos.csv",
    "ingresos": "Resumenes_bot - Ingresos.csv",
}

PAGADO_MARKER = "PAGADO"


# ---------------------------------------------------------------------------
# Parsers / normalización (fuente real, sin modificar archivos)
# ---------------------------------------------------------------------------

def parse_fecha(val: str) -> date | None:
    """dd/mm/yyyy o d/m/yyyy (años 2 o 4 dígitos). None si vacío; raise si inválido."""
    s = (val or "").strip()
    if not s:
        return None
    partes = s.split("/")
    if len(partes) != 3:
        raise ValueError(f"fecha inválida: {val!r}")
    try:
        d, m, y = (int(p) for p in partes)
    except ValueError:
        raise ValueError(f"fecha inválida: {val!r}")
    if y < 100:
        y += 2000
    if not (1 <= m <= 12) or not (1 <= d <= 31):
        raise ValueError(f"fecha inválida: {val!r}")
    return date(y, m, d)


def parse_monto(val: str) -> Decimal | None:
    """Monto ARS/USD del CSV. Soporta $, '.' miles, ',' decimales, negativos.

    Ejemplos: '$1.700.000' -> 1700000.00 | '27110,4' -> 27110.40
              '999,99' -> 999.99 | '-9999,5' -> -9999.50 | '0' -> 0
    Devuelve None para vacío; raise para valores no parseables.
    El marcador 'PAGADO' (dolar de consumos) -> 0 (NO es un monto USD).
    """
    s = (val or "").strip()
    if not s:
        return None
    if s.upper() == PAGADO_MARKER:
        return Decimal("0.00")
    s = s.replace("$", "").replace(" ", "").strip()
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(".", "")
    try:
        return Decimal(s).quantize(Decimal("0.01"))
    except InvalidOperation:
        raise ValueError(f"monto inválido: {val!r}")


def parse_bool_si_no(val: str) -> bool:
    """'SI'/'NO'/'si'/'no' -> bool. Vacío/None -> False (reglas: activo)."""
    return str(val or "").strip().upper() == "SI"


def parse_int(val: str) -> int:
    return int((val or "0").strip() or 0)


def fecha_iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


# ---------------------------------------------------------------------------
# Carga de CSVs
# ---------------------------------------------------------------------------

def load_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    with open(path, encoding="utf-8-sig") as f:
        sample = f.read().replace("\x00", "")
    rows = list(csv.reader(io.StringIO(sample)))
    if not rows:
        return [], []
    header = [h.strip() for h in rows[0]]
    return header, rows[1:]


# ---------------------------------------------------------------------------
# Transformación de cada hoja -> filas para Supabase
# ---------------------------------------------------------------------------

CONFIG_KEYS = ["Hora_Ejecucion", "Last_Telegram_Update_ID", "Telegram_State"]


def transform_config(header: list[str], rows: list[list[str]]) -> list[dict]:
    """Hoja Config: híbrida (config + categorías + categorías ingreso).
    La primera fila de datos contiene los valores de config (columnas
    Hora_Ejecucion/Last_Telegram_Update_ID/Telegram_State); las categorías
    van por columna (Tipo en col E, Tipo_Ingreso en col I).
    Idempotente con ON CONFLICT.
    """
    config_rows = []
    cat_rows = []
    for idx, r in enumerate(rows):
        # config: solo la primera fila de datos (claves del header)
        if idx == 0:
            for ci, clave in enumerate(CONFIG_KEYS):
                if ci < len(r):
                    config_rows.append(
                        {"clave": clave, "valor": r[ci].strip() or "",
                         "descripcion": None}
                    )
        # categorías de gasto (Tipo, col E / índice 4)
        if len(r) > 4 and r[4].strip():
            cat_rows.append(
                {"es_ingreso": False, "tipo": r[4].strip(), "monto_fijo": None,
                 "pertenece": r[6].strip() or "David"}
            )
        # categorías de ingreso (Tipo_Ingreso, col I / índice 8)
        if len(r) > 8 and r[8].strip():
            cat_rows.append(
                {"es_ingreso": True, "tipo": r[8].strip(), "monto_fijo": None,
                 "pertenece": r[6].strip() or "David"}
            )
    return [{"tabla": "config", "row": x} for x in config_rows] + \
           [{"tabla": "categorias_fijas", "row": x} for x in cat_rows]


def transform_datos(header: list[str], rows: list[list[str]]) -> list[dict]:
    out = []
    for r in rows:
        out.append(
            {"tabla": "reglas", "row": {
                "remitente": (r[0] or "").strip() or "Manual Telegram",
                "asunto_contiene": (r[1] or "").strip() or None,
                "clave": (r[2] or "").strip() or None,
                "activo": parse_bool_si_no(r[3]),
                "tiene_adjunto": parse_bool_si_no(r[4]),
                "es_tarjeta_credito": parse_bool_si_no(r[5]),
                "regex_consumo": (r[6] or "").strip() or None,
                "regex_cierre": (r[7] or "").strip() or None,
                "regex_vencimiento": (r[8] or "").strip() or None,
                "regex_monto": (r[9] or "").strip() or None,
                "pertenece": (r[10] or "").strip() or "David",
                "entidad": (r[11] or "").strip() or None,
            }}
        )
    return out


def transform_consolidado(header: list[str], rows: list[list[str]]) -> list[dict]:
    out = []
    for r in rows:
        out.append({"tabla": "consolidado", "row": {
            "fecha_mail": fecha_iso(parse_fecha(r[0])),
            "remitente": (r[1] or "").strip(),
            "asunto": (r[2] or "").strip(),
            "monto_total": parse_monto(r[3]),
            "fecha_vencimiento": fecha_iso(parse_fecha(r[4])),
            "link_drive": (r[5] or "").strip() or None,
            "id_consolidado": (r[6] or "").strip() or None,
            "pertenece": (r[7] or "").strip() or "David",
        }})
    return out


def _comprobante_legacy(r: list[str]) -> str:
    """Comprobante RAW preservando ceros a la izquierda.

    La columna 'Comprobante' fue normalizada por Sheets (USER_ENTERED convierte
    '008452' -> 8452). El comprobante ORIGINAL del parseo del bot quedó en el
    ID_Consumo legacy: '{fecha}|{comprobante}|{detalle}|{cuota}|{remitente}'.
    Para preservar el dato original (y la dedup 1:1 futura con el bot), se usa
    el comprobante del ID legacy.
    """
    idc = (r[10] or "").strip()
    if idc:
        partes = idc.split("|")
        if len(partes) >= 5:
            return partes[1].strip()
    return (r[1] or "").strip()


def transform_consumos(header: list[str], rows: list[list[str]]) -> list[dict]:
    out = []
    for r in rows:
        out.append({"tabla": "consumos", "row": {
            "fecha_consumo": fecha_iso(parse_fecha(r[0])),
            "comprobante": _comprobante_legacy(r),
            "detalle": (r[2] or "").strip(),
            "cuota_actual": parse_int(r[3]),
            "cuota_total": parse_int(r[4]),
            "pesos": parse_monto(r[5]),
            "dolar": parse_monto(r[6]),
            "fecha_cierre": fecha_iso(parse_fecha(r[7])),
            "fecha_vencimiento": fecha_iso(parse_fecha(r[8])),
            "remitente": (r[9] or "").strip(),
            "id_consumo": (r[10] or "").strip() or None,
            "pertenece": (r[11] or "").strip() or "David",
        }})
    return out


def transform_ingresos(header: list[str], rows: list[list[str]]) -> list[dict]:
    out = []
    for r in rows:
        out.append({"tabla": "ingresos", "row": {
            "fecha": fecha_iso(parse_fecha(r[0])),
            "tipo": (r[1] or "").strip(),
            "monto": parse_monto(r[2]),
            "origen": (r[3] or "").strip() or "Manual Telegram",
            "id_ingreso": (r[4] or "").strip() or None,
            "pertenece": (r[5] or "").strip() or "David",
        }})
    return out


TRANSFORMERS = {
    "config": transform_config,
    "datos": transform_datos,
    "consolidado": transform_consolidado,
    "consumos": transform_consumos,
    "ingresos": transform_ingresos,
}


def load_all(csv_dir: Path) -> dict[str, list[dict]]:
    result = {}
    for key, fname in CSV_FILES.items():
        path = csv_dir / fname
        if not path.exists():
            raise FileNotFoundError(f"No existe fuente: {path}")
        header, rows = load_csv(path)
        result[key] = TRANSFORMERS[key](header, rows)
    return result


def summarize(all_data: dict[str, list[dict]]) -> dict[str, int]:
    from collections import Counter
    counts = Counter()
    for rows in all_data.values():
        for item in rows:
            counts[item["tabla"]] += 1
    return dict(counts)


# ---------------------------------------------------------------------------
# Persistencia (idempotente)
# ---------------------------------------------------------------------------

INSERT_SQL = {
    "config": """
        INSERT INTO config (clave, valor, descripcion)
        VALUES (%(clave)s, %(valor)s, %(descripcion)s)
        ON CONFLICT (clave) DO NOTHING
    """,
    "categorias_fijas": """
        INSERT INTO categorias_fijas (es_ingreso, tipo, monto_fijo, pertenece)
        VALUES (%(es_ingreso)s, %(tipo)s, %(monto_fijo)s, %(pertenece)s)
        ON CONFLICT (es_ingreso, tipo) DO NOTHING
    """,
    "reglas": """
        INSERT INTO reglas (
            remitente, asunto_contiene, clave, activo, tiene_adjunto,
            es_tarjeta_credito, regex_consumo, regex_cierre, regex_vencimiento,
            regex_monto, pertenece, entidad
        ) VALUES (
            %(remitente)s, %(asunto_contiene)s, %(clave)s, %(activo)s,
            %(tiene_adjunto)s, %(es_tarjeta_credito)s, %(regex_consumo)s,
            %(regex_cierre)s, %(regex_vencimiento)s, %(regex_monto)s,
            %(pertenece)s, %(entidad)s
        )
        ON CONFLICT DO NOTHING
    """,
    "consolidado": """
        INSERT INTO consolidado (
            fecha_mail, remitente, asunto, monto_total, fecha_vencimiento,
            link_drive, id_consolidado, pertenece
        ) VALUES (
            %(fecha_mail)s, %(remitente)s, %(asunto)s, %(monto_total)s,
            %(fecha_vencimiento)s, %(link_drive)s, %(id_consolidado)s,
            %(pertenece)s
        )
        ON CONFLICT DO NOTHING
    """,
    "consumos": """
        INSERT INTO consumos (
            fecha_consumo, comprobante, detalle, cuota_actual, cuota_total,
            pesos, dolar, fecha_cierre, fecha_vencimiento, remitente,
            id_consumo, pertenece
        ) VALUES (
            %(fecha_consumo)s, %(comprobante)s, %(detalle)s, %(cuota_actual)s,
            %(cuota_total)s, %(pesos)s, %(dolar)s, %(fecha_cierre)s,
            %(fecha_vencimiento)s, %(remitente)s, %(id_consumo)s,
            %(pertenece)s
        )
        ON CONFLICT DO NOTHING
    """,
    "ingresos": """
        INSERT INTO ingresos (
            fecha, tipo, monto, origen, id_ingreso, pertenece
        ) VALUES (
            %(fecha)s, %(tipo)s, %(monto)s, %(origen)s, %(id_ingreso)s,
            %(pertenece)s
        )
        ON CONFLICT DO NOTHING
    """,
}


def get_conn():
    import psycopg2

    url = os.environ.get("SUPABASE_DB_URL")
    if url:
        return psycopg2.connect(url, connect_timeout=10)
    pw = os.environ["SUPABASE_DBPW"]
    return psycopg2.connect(
        host="aws-0-sa-east-1.pooler.supabase.com", port=6543,
        dbname="postgres", user="postgres.zargsvnssplbwkkixjos",
        password=pw, sslmode="require", connect_timeout=10,
    )


def execute_inserts(all_data: dict[str, list[dict]]) -> dict[str, int]:
    conn = get_conn()
    inserted = {}
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            for item in all_data["config"]:
                if item["tabla"] != "config":
                    continue
                cur.execute(INSERT_SQL["config"], item["row"])
            for item in all_data["datos"]:
                cur.execute(INSERT_SQL["reglas"], item["row"])
            for tabla in ("consolidado", "consumos", "ingresos"):
                rows = [it["row"] for it in all_data[tabla] if it["tabla"] == tabla]
                inserted[tabla] = 0
                for row in rows:
                    cur.execute(INSERT_SQL[tabla], row)
                    inserted[tabla] += cur.rowcount
            for tabla in ("categorias_fijas",):
                rows = [it["row"] for it in all_data["config"]
                        if it["tabla"] == "categorias_fijas"]
                inserted[tabla] = 0
                for row in rows:
                    cur.execute(INSERT_SQL[tabla], row)
                    inserted[tabla] += cur.rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return inserted


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Migración CSVs -> Supabase (FASE 2)")
    ap.add_argument("--dry-run", action="store_true", help="parsea y valida sin insertar")
    ap.add_argument("--csv-dir", type=str, default=str(DEFAULT_CSV_DIR))
    ap.add_argument("--check-env", action="store_true", help="verifica SUPABASE_DB_URL/DBPW")
    args = ap.parse_args(argv)

    csv_dir = Path(args.csv_dir)
    print(f"[FASE2] Fuente: {csv_dir}")

    all_data = load_all(csv_dir)
    counts = summarize(all_data)
    print("[FASE2] Conteos fuente (transformados):")
    for tabla in ("config", "categorias_fijas", "reglas",
                  "consolidado", "consumos", "ingresos"):
        print(f"  {tabla:20s} {counts.get(tabla, 0)}")

    if args.check_env and not args.dry_run:
        env_ok = bool(os.environ.get("SUPABASE_DB_URL") or os.environ.get("SUPABASE_DBPW"))
        print(f"[FASE2] env SUPABASE_*: {'OK' if env_ok else 'FALTA'}")
        if not env_ok:
            return 1

    if args.dry_run:
        print("[FASE2] DRY-RUN OK (no se insertó nada)")
        return 0

    inserted = execute_inserts(all_data)
    print("[FASE2] Insertados (fila):", inserted)
    print("[FASE2] Migración COMPLETA.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
