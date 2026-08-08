"""Capa de acceso a datos Supabase (FASE 3 + FASE 4).

Arquitectura:
    bot_Saldo.py
        ↓
    supabase_client.py   (repositorio / data access)
        ↓
    Supabase (PostgreSQL)

Esta capa NO mezcla SQL con lógica de negocio: expone funciones que devuelven
los mismos dicts que bot_Saldo.py obtiene hoy de Google Sheets, para que la
equivalencia Sheets <-> Supabase sea 1:1.

FASE 3 (lectura): las consultas pasan por _read_only(); cualquier intento de
INSERT/UPDATE/DELETE/DDL se detecta y lanza excepción.

FASE 4 (escritura): las funciones *escriben* en las tablas de negocio
(consolidado, consumos, ingresos) con UPSERT idempotente apoyado en los
UNIQUE INDEX de dedup del schema (integridad garantizada por PostgreSQL aún
con dos ejecuciones simultáneas: ON CONFLICT + transacción + catch de
UniqueViolation). NO normalizan comprobantes (raw) ni cambian la lógica de
negocio del bot. NO escriben en Sheets (la capa no conoce Sheets).

Estrategia de fallback (documentada en FASE 4):
    * Lecturas: bot_Saldo.py usa Sheets como respaldo explícito.
    * Escrituras: NO hay fallback silencioso a Sheets (evita doble escritura /
      inconsistencia). Si Supabase falla, se lanza SupabaseWriteError con el
      mensaje '[SUPABASE WRITE ERROR]' visible; el bot lo propaga al error
      handler (Telegram) y NO inserta en Sheets.

Configuración (nunca hardcodeada):
    SUPABASE_DB_URL   - connection string completa (postgresql://...). Preferida.
    SUPABASE_DBPW     - alternativa: solo la contraseña (build pooler).

Errores (explícitos, no se ocultan):
    SupabaseNotConfiguredError - faltan SUPABASE_DB_URL / SUPABASE_DBPW
    SupabaseReadError          - fallo de conexión / consulta / datos inválidos
    SupabaseWriteError         - fallo de escritura / datos inválidos al escribir
bot_Saldo.py decide el fallback a Sheets y avisa "[SUPABASE READ ERROR]".
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

# ---------------------------------------------------------------------------
# Errores explícitos
# ---------------------------------------------------------------------------

class SupabaseError(Exception):
    """Base de errores de la capa Supabase."""


class SupabaseNotConfiguredError(SupabaseError):
    """Faltan credenciales de entorno (SUPABASE_DB_URL o SUPABASE_DBPW)."""


class SupabaseReadError(SupabaseError):
    """Fallo de conexión, consulta o formato de datos."""


class SupabaseWriteError(SupabaseError):
    """Fallo de conexión, escritura o formato de datos al escribir."""


# ---------------------------------------------------------------------------
# Conexión (lazy, desde entorno; nunca hardcodeada)
# ---------------------------------------------------------------------------

_POOLER_HOST = "aws-0-sa-east-1.pooler.supabase.com"
_POOLER_PORT = 6543
_POOLER_DB = "postgres"
_POOLER_USER = "postgres.zargsvnssplbwkkixjos"


def _build_dsn() -> str:
    url = os.environ.get("SUPABASE_DB_URL", "").strip()
    if url:
        return url
    pw = os.environ.get("SUPABASE_DBPW", "").strip()
    if not pw:
        raise SupabaseNotConfiguredError(
            "Faltan SUPABASE_DB_URL o SUPABASE_DBPW en el entorno"
        )
    return (
        f"postgresql://{_POOLER_USER}:{pw}@{_POOLER_HOST}:{_POOLER_PORT}/{_POOLER_DB}"
        "?sslmode=require"
    )


def _get_conn(for_write: bool = False):
    error_cls = SupabaseWriteError if for_write else SupabaseReadError
    try:
        import psycopg2
    except ImportError as exc:  # pragma: no cover - depende del entorno
        raise error_cls(f"psycopg2 no instalado: {exc}")
    try:
        return psycopg2.connect(_build_dsn(), connect_timeout=10)
    except SupabaseError:
        raise
    except Exception as exc:
        raise error_cls(f"Fallo de conexión a Supabase: {exc}") from exc


# ---------------------------------------------------------------------------
# Seguridad: solo lectura
# ---------------------------------------------------------------------------

_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|GRANT|REVOKE|CREATE|MERGE)\b",
    re.IGNORECASE,
)


def _read_only(sql: str) -> str:
    """Rechaza cualquier SQL que no sea una lectura."""
    if _FORBIDDEN_KEYWORDS.search(sql):
        raise SupabaseReadError(
            "Escritura bloqueada: la capa FASE 3 es SOLO LECTURA"
        )
    return sql


def _fetch_all(sql: str, params: tuple | None = None) -> list[tuple]:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(_read_only(sql), params)
            return cur.fetchall()
    except SupabaseError:
        raise
    except Exception as exc:
        raise SupabaseReadError(f"Fallo en consulta Supabase: {exc}") from exc
    finally:
        conn.close()


def _fetch_one(sql: str, params: tuple | None = None):
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(_read_only(sql), params)
            return cur.fetchone()
    except SupabaseError:
        raise
    except Exception as exc:
        raise SupabaseReadError(f"Fallo en consulta Supabase: {exc}") from exc
    finally:
        conn.close()


def supabase_disponible() -> bool:
    """True si hay credenciales configuradas en el entorno."""
    return bool(os.environ.get("SUPABASE_DB_URL") or os.environ.get("SUPABASE_DBPW"))


# ---------------------------------------------------------------------------
# REGLAS (tabla reglas) — formato del bot (obtener_reglas)
# ---------------------------------------------------------------------------

def obtener_reglas() -> list[dict]:
    """Reglas ACTIVAS con el mismo formato que obtener_reglas() de Sheets.

    Bot usa: Remitente, Asunto_Contiene, Clave, Activo, Tiene_Adjunto,
    Es_Tarjeta_Credito, Regex_Consumo, Regex_Cierre, Regex_Vencimiento,
    Regex_Monto. FASE 3 también conserva Pertenece y Entidad (exigencia).
    """
    sql = """
        SELECT remitente, asunto_contiene, clave, tiene_adjunto,
               es_tarjeta_credito, regex_consumo, regex_cierre,
               regex_vencimiento, regex_monto, pertenece, entidad
        FROM reglas
        WHERE activo = TRUE
        ORDER BY id
    """
    filas = _fetch_all(sql)
    reglas = []
    for r in filas:
        reglas.append({
            "Remitente": (r[0] or "").strip(),
            "Asunto_Contiene": (r[1] or "").strip(),
            "Clave": (r[2] or "").strip(),
            "Activo": "SI",
            "Tiene_Adjunto": "SI" if r[3] else "NO",
            "Es_Tarjeta_Credito": "SI" if r[4] else "NO",
            "Regex_Consumo": (r[5] or "").strip(),
            "Regex_Cierre": (r[6] or "").strip(),
            "Regex_Vencimiento": (r[7] or "").strip(),
            "Regex_Monto": (r[8] or "").strip(),
            "Pertenece": (r[9] or "").strip(),
            "Entidad": (r[10] or "").strip(),
        })
    return reglas


# ---------------------------------------------------------------------------
# CONFIG (tabla config) — pares clave/valor
# ---------------------------------------------------------------------------

def obtener_config() -> dict[str, str]:
    """Todos los pares clave/valor de la tabla config."""
    filas = _fetch_all("SELECT clave, valor FROM config")
    return {str(k): str(v or "") for k, v in filas}


def obtener_config_valor(clave: str) -> str:
    """Valor de una clave de config ('' si no existe)."""
    fila = _fetch_one("SELECT valor FROM config WHERE clave = %s", (clave,))
    return str(fila[0] or "").strip() if fila else ""


def obtener_config_telegram() -> tuple[int, str]:
    """(last_update_id, telegram_state) equivalentes a leer_config_completo."""
    cfg = obtener_config()
    try:
        last_update_id = int(cfg.get("Last_Telegram_Update_ID", "") or 0)
    except ValueError:
        last_update_id = 0
    state = cfg.get("Telegram_State", "").strip()
    return last_update_id, state


# ---------------------------------------------------------------------------
# CATEGORÍAS (tabla categorias_fijas) — tipos de gasto e ingreso
# ---------------------------------------------------------------------------

def _obtener_categorias() -> list[dict]:
    """Filas de categorias_fijas en orden de la hoja (id)."""
    filas = _fetch_all(
        "SELECT es_ingreso, tipo, monto_fijo, pertenece FROM categorias_fijas "
        "ORDER BY id"
    )
    return [
        {
            "es_ingreso": bool(r[0]),
            "tipo": (r[1] or "").strip(),
            "monto_fijo": r[2],
            "pertenece": (r[3] or "").strip(),
        }
        for r in filas
    ]


def obtener_tipos() -> tuple[list[str], list[str]]:
    """(tipos_gastos, tipos_ingresos) — orden de la hoja Config."""
    categorias = _obtener_categorias()
    gastos = [c["tipo"] for c in categorias if not c["es_ingreso"]]
    ingresos = [c["tipo"] for c in categorias if c["es_ingreso"]]
    return gastos, ingresos


def obtener_fijos() -> tuple[list[dict], list[dict]]:
    """(gastos_fijos, ingresos_fijos) con monto > 0 (equivalente a
    procesar_fijos_mensuales). Cada ítem: {"tipo": str, "monto": float}.
    """
    categorias = _obtener_categorias()
    gastos = [
        {"tipo": c["tipo"], "monto": _a_float(c["monto_fijo"])}
        for c in categorias
        if not c["es_ingreso"] and _monto_positivo(c["monto_fijo"])
    ]
    ingresos = [
        {"tipo": c["tipo"], "monto": _a_float(c["monto_fijo"])}
        for c in categorias
        if c["es_ingreso"] and _monto_positivo(c["monto_fijo"])
    ]
    return gastos, ingresos


def _monto_positivo(v) -> bool:
    if v is None:
        return False
    try:
        return float(v) > 0
    except (TypeError, ValueError):
        return False


def _a_float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def obtener_config_completo() -> tuple[int, str, list[str], list[str]]:
    """Equivalente directo a leer_config_completo(ws_config):
    (last_update_id, state, tipos_gastos, tipos_ingresos)."""
    last_update_id, state = obtener_config_telegram()
    gastos, ingresos = obtener_tipos()
    return last_update_id, state, gastos, ingresos


# ---------------------------------------------------------------------------
# FASE 4 — ESCRITURA
#
# UPSERT idempotente apoyado en los UNIQUE INDEX de dedup del schema:
#   * consolidado: lower(remitente), fecha_vencimiento, monto_total
#   * consumos:    fecha_consumo, comprobante, detalle, cuota_total, remitente
#   * ingresos:    fecha, tipo, origen
# La integridad la garantiza PostgreSQL aún con dos procesos simultáneos
# (cron + dispatch): ON CONFLICT + transacción + catch de UniqueViolation.
# NO normaliza comprobantes (raw) y NO escribe en Sheets.
# ---------------------------------------------------------------------------

def _a_fecha(v):
    """'dd/mm/yyyy' -> 'yyyy-mm-dd' (tolera '.', '-' y años de 2 dígitos).
    Acepta date/datetime e ISO 'yyyy-mm-dd'. None si no se puede parsear."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, date):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip()
    if not s:
        return None
    m = re.match(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})$", s)
    if m:
        d, mo, y = m.groups()
        if len(y) == 2:
            y = "20" + y
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
    if m:
        return s
    import email.utils

    try:
        dt = email.utils.parsedate_to_datetime(s)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None


def _a_monto(v) -> float:
    """Misma normalización que bot_Saldo.normalizar_monto (1:1 con el bot)."""
    t = str(v).replace("$", "").strip()
    if "." in t and "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif "," in t:
        t = t.replace(",", ".")
    return round(float(t), 2)


def _a_int(v) -> int | None:
    if v is None:
        return None
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return None


def existe_consolidado(remitente, monto_total, fecha_vencimiento) -> bool:
    """True si ya existe un resumen con la clave de dedup del bot
    (lower(remitente)|vto|monto), 1:1 con es_registro_duplicado()."""
    f_vto = _a_fecha(fecha_vencimiento)
    if f_vto is None:
        return False
    try:
        monto = _a_monto(monto_total)
    except (TypeError, ValueError):
        return False
    fila = _fetch_one(
        """
        SELECT 1 FROM consolidado
        WHERE lower(remitente) = lower(%s)
          AND fecha_vencimiento = %s
          AND monto_total = %s
        LIMIT 1
        """,
        (str(remitente).strip(), f_vto, monto),
    )
    return fila is not None


def _write_in_transaction(fn, reintentos: int = 1):
    """Ejecuta fn(cur) en una transacción y devuelve su resultado.

    Concurrencia: si otro proceso insertó la misma fila entre medio
    (UniqueViolation 23505), reintenta la operación: el conflicto ya quedó
    resuelto por el otro proceso y el INSERT ON CONFLICT pasa a no-op.
    """
    import psycopg2

    for _ in range(reintentos + 1):
        conn = _get_conn(for_write=True)
        try:
            with conn.cursor() as cur:
                resultado = fn(cur)
            conn.commit()
            return resultado
        except SupabaseError as exc:
            conn.rollback()
            raise
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            continue
        except Exception as exc:
            conn.rollback()
            raise SupabaseWriteError(
                f"[SUPABASE WRITE ERROR] {exc}"
            ) from exc
        finally:
            conn.close()
    raise SupabaseWriteError(
        "[SUPABASE WRITE ERROR] conflicto de unicidad concurrente no resuelto"
    )


def guardar_consolidado(
    fecha_mail, remitente, asunto, monto_total,
    fecha_vencimiento, link_drive="", pertenece="David",
) -> str:
    """Equivalente 1:1 a guardar_en_sheet() + es_registro_duplicado():
    inserta el resumen en 'consolidado' con UPSERT idempotente. Si ya existe
    (mismo lower(remitente)|vto|monto) devuelve 'existente' sin duplicar.
    Devuelve 'insertado' | 'existente'."""
    f_mail = _a_fecha(fecha_mail)
    f_vto = _a_fecha(fecha_vencimiento)
    if f_mail is None or f_vto is None:
        raise SupabaseWriteError(
            "[SUPABASE WRITE ERROR] fecha inválida en consolidado: "
            f"fecha_mail={fecha_mail!r}, fecha_vencimiento={fecha_vencimiento!r}"
        )
    try:
        monto = _a_monto(monto_total)
    except (TypeError, ValueError) as exc:
        raise SupabaseWriteError(
            "[SUPABASE WRITE ERROR] monto inválido en consolidado: "
            f"{monto_total!r}"
        ) from exc
    rem = str(remitente).strip()
    id_consolidado = f"{rem.lower()}|{str(fecha_vencimiento).strip()}|{monto}"

    def _insertar(cur):
        cur.execute(
            """
            INSERT INTO consolidado
                (fecha_mail, remitente, asunto, monto_total,
                 fecha_vencimiento, link_drive, id_consolidado, pertenece)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (lower(remitente), fecha_vencimiento, monto_total)
            DO NOTHING
            """,
            (f_mail, rem, str(asunto).strip(), monto, f_vto,
             link_drive or "", id_consolidado, pertenece),
        )
        return "insertado" if cur.rowcount == 1 else "existente"

    return _write_in_transaction(_insertar)


def guardar_o_actualizar_consumos(consumos, remitente) -> list[dict]:
    """Equivalente 1:1 a guardar_o_actualizar_consumos_sheet():
      - si el consumo no existe -> lo inserta;
      - si existe y cuota_actual nueva >= existente -> actualiza
        cuota_actual, pesos, dolar, fecha_cierre, fecha_vencimiento;
      - si cuota nueva < existente -> NO retrocede (conserva los valores).
    Comprobante RAW (sin normalizar; '008452' se conserva como hoy).
    Devuelve una lista (mismo orden que la entrada) con:
      {'estado': 'insertado'|'actualizado'|'sin_cambios', 'fecha',
       'comprobante', 'detalle', 'cuota_actual', 'pesos'}"""
    if not consumos:
        return []
    rem = str(remitente).strip()

    preparados = []
    for c in consumos:
        f_raw = str(c.get("fecha", "")).strip()
        comp = str(c.get("comprobante", "")).strip()
        det = str(c.get("detalle", "")).strip()
        cuota_act = _a_int(c.get("cuota_actual"))
        cuota_tot = _a_int(c.get("cuota_total"))
        f = _a_fecha(f_raw)
        pesos = _a_monto(c.get("pesos") if c.get("pesos") is not None else 0)
        dolar = _a_monto(c.get("dolar") if c.get("dolar") is not None else 0)
        f_cierre = _a_fecha(c.get("fecha_cierre"))
        f_vto = _a_fecha(c.get("fecha_vencimiento"))
        if f is None or cuota_act is None or cuota_tot is None:
            raise SupabaseWriteError(
                "[SUPABASE WRITE ERROR] datos inválidos en consumo: "
                f"{c!r}"
            )
        preparados.append({
            "f_raw": f_raw, "comp": comp, "det": det,
            "cuota_act": cuota_act, "cuota_tot": cuota_tot,
            "pesos": pesos, "dolar": dolar,
            "f_cierre": f_cierre, "f_vto": f_vto, "f": f, "rem": rem,
        })

    def _lote(cur):
        resultados = []
        for p in preparados:
            id_consumo = (
                f"{p['f_raw']}|{p['comp']}|{p['det']}|{p['cuota_tot']}|{p['rem']}"
            )
            cur.execute(
                """
                INSERT INTO consumos
                    (fecha_consumo, comprobante, detalle, cuota_actual,
                     cuota_total, pesos, dolar, fecha_cierre,
                     fecha_vencimiento, remitente, id_consumo, pertenece)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT
                    (fecha_consumo, comprobante, detalle, cuota_total, remitente)
                DO NOTHING
                """,
                (p["f"], p["comp"], p["det"], p["cuota_act"], p["cuota_tot"],
                 p["pesos"], p["dolar"], p["f_cierre"], p["f_vto"], p["rem"],
                 id_consumo, "David"),
            )
            if cur.rowcount == 1:
                resultados.append({
                    "estado": "insertado",
                    "fecha": p["f_raw"], "comprobante": p["comp"],
                    "detalle": p["det"], "cuota_actual": p["cuota_act"],
                    "pesos": p["pesos"],
                })
                continue
            # Ya existía: solo avanza si la cuota nueva >= existente (no retrocede).
            cur.execute(
                """
                UPDATE consumos SET
                    cuota_actual      = %(ca)s,
                    pesos             = %(pe)s,
                    dolar             = %(do)s,
                    fecha_cierre      = %(fc)s,
                    fecha_vencimiento = %(fv)s
                WHERE fecha_consumo = %(f)s AND comprobante = %(co)s
                  AND detalle = %(de)s AND cuota_total = %(ct)s
                  AND remitente = %(re)s AND cuota_actual <= %(ca)s
                """,
                {"ca": p["cuota_act"], "pe": p["pesos"], "do": p["dolar"],
                 "fc": p["f_cierre"], "fv": p["f_vto"],
                 "f": p["f"], "co": p["comp"], "de": p["det"],
                 "ct": p["cuota_tot"], "re": p["rem"]},
            )
            estado = "actualizado" if cur.rowcount == 1 else "sin_cambios"
            resultados.append({
                "estado": estado,
                "fecha": p["f_raw"], "comprobante": p["comp"],
                "detalle": p["det"], "cuota_actual": p["cuota_act"],
                "pesos": p["pesos"],
            })
        return resultados

    return _write_in_transaction(_lote)


def guardar_ingreso(fecha, tipo, monto, origen="Manual Telegram") -> str:
    """Equivalente a la escritura de 'Ingresos' del bot (manual Telegram y
    fijos). UPSERT idempotente sobre (fecha, tipo, origen): devuelve
    'insertado' | 'existente'. El ID legacy es reproducible e igual que hoy:
    fecha|Ingreso|tipo|origen."""
    f = _a_fecha(fecha)
    tipo_s = str(tipo).strip()
    origen_s = str(origen).strip()
    if f is None or not tipo_s or not origen_s:
        raise SupabaseWriteError(
            "[SUPABASE WRITE ERROR] datos inválidos en ingreso: "
            f"fecha={fecha!r}, tipo={tipo!r}, origen={origen!r}"
        )
    try:
        monto_f = _a_monto(monto)
    except (TypeError, ValueError) as exc:
        raise SupabaseWriteError(
            "[SUPABASE WRITE ERROR] monto inválido en ingreso: "
            f"{monto!r}"
        ) from exc
    id_ingreso = f"{str(fecha).strip()}|Ingreso|{tipo_s}|{origen_s}"

    def _insertar(cur):
        cur.execute(
            """
            INSERT INTO ingresos (fecha, tipo, monto, origen, id_ingreso, pertenece)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (fecha, tipo, origen) DO NOTHING
            """,
            (f, tipo_s, monto_f, origen_s, id_ingreso, "David"),
        )
        return "insertado" if cur.rowcount == 1 else "existente"

    return _write_in_transaction(_insertar)


# ---------------------------------------------------------------------------
# FASE 5 — GMAIL / idempotencia por mensaje_id
#
# La tabla mensajes_procesados reemplaza al label Gmail como persistencia
# principal del estado "ya procesado". El label puede seguir existiendo como
# respaldo / señal visual en Gmail, pero la fuente de verdad es Supabase.
# ---------------------------------------------------------------------------

def mensaje_ya_procesado(mensaje_id) -> bool:
    """True si el mensaje Gmail ya fue registrado en mensajes_procesados."""
    mensaje = str(mensaje_id or "").strip()
    if not mensaje:
        return False
    fila = _fetch_one(
        "SELECT 1 FROM mensajes_procesados WHERE mensaje_id = %s LIMIT 1",
        (mensaje,),
    )
    return fila is not None


def registrar_mensaje_procesado(mensaje_id, remitente="", asunto="") -> str:
    """Registra un mensaje Gmail como procesado.

    UPSERT idempotente sobre UNIQUE(mensaje_id). Devuelve:
        'insertado' | 'existente'
    """
    mensaje = str(mensaje_id or "").strip()
    if not mensaje:
        raise SupabaseWriteError(
            "[SUPABASE WRITE ERROR] mensaje_id inválido en mensajes_procesados"
        )
    rem = str(remitente or "").strip()
    subj = str(asunto or "").strip()

    def _insertar(cur):
        cur.execute(
            """
            INSERT INTO mensajes_procesados (mensaje_id, remitente, asunto)
            VALUES (%s, %s, %s)
            ON CONFLICT (mensaje_id) DO NOTHING
            """,
            (mensaje, rem, subj),
        )
        return "insertado" if cur.rowcount == 1 else "existente"

    return _write_in_transaction(_insertar)
