import os
import re
import io
import json
import sys
import time
import base64
import email
import email.header
import email.utils
import html
from datetime import datetime
import traceback
from zoneinfo import ZoneInfo

import requests
import gspread
import pikepdf
import pdfplumber
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# FASE 3: capa de acceso a Supabase (lecturas). Se resuelve el módulo desde el
# mismo directorio del bot (independiente del cwd de ejecución). NO se eliminan
# aún GOOGLE_* / SHEET_ID: Sheets queda como respaldo explícito de cada lectura.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import supabase_client

print("[INICIO] Inicializando bot_Saldo.py de Producción...")

# ---------- Configuración desde variables de entorno (secrets) ----------
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
SHEET_ID = os.environ["SHEET_ID"]
SHEET_NAME = "Consolidado"
LABEL_PROCESADO = "Procesado-Resumen"

# Variables globales para servicios de carga diferida (Lazy Loading)
_creds = None
_gc = None
_gmail_service = None
_drive_service = None


def get_credentials():
    global _creds
    if _creds is None:
        _creds = Credentials(
            token=None,
            refresh_token=os.environ["GOOGLE_REFRESH_TOKEN"],
            client_id=os.environ["GOOGLE_CLIENT_ID"],
            client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
            token_uri="https://oauth2.googleapis.com/token",
            scopes=None,
        )
    return _creds


def get_gc():
    global _gc
    if _gc is None:
        _gc = gspread.authorize(get_credentials())
        print("[DEBUG] Gspread client inicializado bajo demanda")
    return _gc


def get_gmail_service():
    global _gmail_service
    if _gmail_service is None:
        _gmail_service = build("gmail", "v1", credentials=get_credentials())
        print("[DEBUG] Gmail service inicializado bajo demanda")
    return _gmail_service


def get_drive_service():
    global _drive_service
    if _drive_service is None:
        _drive_service = build("drive", "v3", credentials=get_credentials())
        print("[DEBUG] Drive service inicializado bajo demanda")
    return _drive_service


def _leer_con_supabase(func, nombre):
    """Ejecuta una lectura contra Supabase (FASE 3).

    Devuelve (ok, resultado). Si Supabase no está configurado o la lectura
    falla, imprime '[SUPABASE READ ERROR]' (error explícito, nunca ocultado) y
    devuelve (False, None) para que el llamador use Google Sheets como
    respaldo (fallback documentado).
    """
    if not supabase_client.supabase_disponible():
        print(
            "[SUPABASE READ ERROR] Supabase no configurado (faltan "
            "SUPABASE_DB_URL o SUPABASE_DBPW). "
            f"Usando Google Sheets como respaldo para: {nombre}"
        )
        return False, None
    try:
        return True, func()
    except supabase_client.SupabaseError as exc:
        print(
            f"[SUPABASE READ ERROR] {nombre}: {exc}. "
            "Usando Google Sheets como respaldo."
        )
        return False, None


def debe_ejecutar_ahora():
    forzar = os.environ.get("FORZAR_EJECUCION", "false").lower()
    if forzar == "true":
        return True

    hora_deseada = ""
    ok, hora_supabase = _leer_con_supabase(
        lambda: supabase_client.obtener_config_valor("Hora_Ejecucion"),
        "config Hora_Ejecucion",
    )
    if ok:
        # Valor raw preservado (p. ej. '12' sin ':'); NO se corrige en FASE 3.
        hora_deseada = hora_supabase
    else:
        sh = get_gc().open_by_key(SHEET_ID)
        ws_config = sh.worksheet("Config")
        filas = ws_config.get_all_records()
        if not filas:
            return True
        hora_deseada = str(filas[0].get("Hora_Ejecucion", "")).strip()
    if not hora_deseada:
        return True

    ahora = datetime.now(ZoneInfo("America/Argentina/Cordoba"))
    try:
        hora_h, hora_m = map(int, hora_deseada.split(":"))
    except ValueError:
        return True

    deseado_minutos = hora_h * 60 + hora_m
    actual_minutos = ahora.hour * 60 + ahora.minute
    return abs(actual_minutos - deseado_minutos) <= 7


def enviar_telegram(mensaje):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": mensaje})


def obtener_o_crear_label(nombre):
    labels = get_gmail_service().users().labels().list(userId="me").execute().get("labels", [])
    for l in labels:
        if l["name"] == nombre:
            return l["id"]
    nuevo = get_gmail_service().users().labels().create(
        userId="me",
        body={"name": nombre, "labelListVisibility": "labelShow", "messageListVisibility": "show"},
    ).execute()
    return nuevo["id"]


def obtener_valor_clave_flexible(registro, clave_buscada):
    for k, v in registro.items():
        if str(k).strip().lower() == clave_buscada.strip().lower():
            return str(v).strip()
    return ""


def obtener_reglas():
    print("[DEBUG] Leyendo reglas desde Supabase...")
    ok, reglas_supabase = _leer_con_supabase(supabase_client.obtener_reglas, "reglas")
    if ok:
        # supabase_client.obtener_reglas devuelve SOLO reglas activas
        # (WHERE activo = TRUE), igual que el filtro del bot (Activo == 'SI').
        return reglas_supabase

    print("[DEBUG] Fallback: Leyendo reglas de la hoja Datos...")
    sh = get_gc().open_by_key(SHEET_ID)
    ws_datos = sh.worksheet("Datos")
    filas = ws_datos.get_all_records()
    reglas_activas = []

    for f in filas:
        activo = obtener_valor_clave_flexible(f, "Activo").upper()
        if activo == "SI":
            regla_norm = {
                "Remitente": obtener_valor_clave_flexible(f, "Remitente"),
                "Asunto_Contiene": obtener_valor_clave_flexible(f, "Asunto_Contiene"),
                "Clave": obtener_valor_clave_flexible(f, "Clave"),
                "Activo": "SI",
                "Tiene_Adjunto": obtener_valor_clave_flexible(f, "Tiene_Adjunto").upper() or "NO",
                "Es_Tarjeta_Credito": obtener_valor_clave_flexible(f, "Es_Tarjeta_Credito").upper() or "NO",
                "Regex_Consumo": obtener_valor_clave_flexible(f, "Regex_Consumo"),
                "Regex_Cierre": obtener_valor_clave_flexible(f, "Regex_Cierre"),
                "Regex_Vencimiento": obtener_valor_clave_flexible(f, "Regex_Vencimiento"),
                "Regex_Monto": obtener_valor_clave_flexible(f, "Regex_Monto"),
            }
            reglas_activas.append(regla_norm)
    return reglas_activas


def buscar_mails_nuevos(remitente, asunto_contiene, excluir_label_procesado=False):
    query = f"from:{remitente}"
    if excluir_label_procesado:
        query += f" -label:{LABEL_PROCESADO}"
    if asunto_contiene:
        query += f' subject:"{asunto_contiene}"'
    resultados = get_gmail_service().users().messages().list(userId="me", q=query).execute()
    return resultados.get("messages", [])


def limpiar_html(texto_html):
    texto = re.sub(r"<[^>]+>", " ", texto_html)
    texto = re.sub(r"&nbsp;|&zwnj;", " ", texto)
    texto = re.sub(r"\s+", " ", texto)
    return texto.strip()


def extraer_datos_mensaje_mime(mensaje_id):
    msg = get_gmail_service().users().messages().get(userId="me", id=mensaje_id, format="raw").execute()
    msg_bytes = base64.urlsafe_b64decode(msg["raw"])
    mime_msg = email.message_from_bytes(msg_bytes)
    
    asunto_raw = mime_msg["Subject"] or "(sin asunto)"
    asunto = ""
    for part, encoding in email.header.decode_header(asunto_raw):
        if isinstance(part, bytes):
            asunto += part.decode(encoding or "utf-8", errors="ignore")
        else:
            asunto += str(part)
            
    fecha = mime_msg["Date"] or ""
    cuerpo_html = ""
    cuerpo_plain = ""
    pdf_filename = None
    pdf_bytes = None
    
    for part in mime_msg.walk():
        content_type = part.get_content_type()
        disposition = str(part.get("Content-Disposition", ""))
        
        if "attachment" in disposition and part.get_filename() and part.get_filename().lower().endswith(".pdf"):
            pdf_filename = part.get_filename()
            pdf_bytes = part.get_payload(decode=True)
        elif part.get_filename() and part.get_filename().lower().endswith(".pdf"):
            pdf_filename = part.get_filename()
            pdf_bytes = part.get_payload(decode=True)
            
        if content_type == "text/html":
            cuerpo_html = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="ignore")
        elif content_type == "text/plain":
            cuerpo_plain = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="ignore")
            
    cuerpo_raw = cuerpo_html or cuerpo_plain or ""
    cuerpo_texto = limpiar_html(cuerpo_raw)
    return asunto, fecha, cuerpo_texto, cuerpo_raw, pdf_filename, pdf_bytes


def descargar_pdf_desde_link(cuerpo_raw):
    urls = re.findall(r'https?://[^\s"\'>]+', cuerpo_raw, re.IGNORECASE)
    candidatos = [u for u in urls if any(k in u.lower() for k in ["api/reportes", "descargar", "factura", "download", "pdf", "print"])]
    if not candidatos and urls:
        candidatos = urls

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    for url in candidatos:
        url_limpia = html.unescape(url)
        try:
            resp = requests.get(url_limpia, headers=headers, timeout=12, stream=True)
            content_type = resp.headers.get("Content-Type", "").lower()
            if "application/pdf" in content_type or resp.content[:4] == b"%PDF":
                return "Factura_Digital.pdf", resp.content
        except Exception:
            continue
    return None, None


def quitar_clave_pdf(pdf_bytes, clave):
    pdf = pikepdf.open(io.BytesIO(pdf_bytes), password=clave)
    salida = io.BytesIO()
    pdf.save(salida)
    pdf.close()
    salida.seek(0)
    return salida.read()


def subir_a_drive(nombre_archivo, pdf_bytes):
    media = MediaIoBaseUpload(io.BytesIO(pdf_bytes), mimetype="application/pdf")
    archivo = get_drive_service().files().create(
        body={"name": nombre_archivo}, media_body=media, fields="id, webViewLink"
    ).execute()
    get_drive_service().permissions().create(
        fileId=archivo["id"], body={"role": "reader", "type": "anyone"}
    ).execute()
    return archivo.get("webViewLink")


def subir_pdf(nombre_archivo, pdf_bytes, remitente):
    """FASE 6: Storage privado como principal, Drive como fallback temporal.

    Devuelve (link_guardado_en_db, link_para_telegram).
    - Storage: guarda `storage://pdfs/<path>` en DB y signed URL en Telegram.
    - Drive fallback: usa la misma URL para DB y Telegram.
    - EPEC: no debe llamar a esta función (caller deja links vacíos).
    """
    try:
        resultado = supabase_client.subir_pdf_storage(nombre_archivo, pdf_bytes, remitente)
        return resultado["link_ref"], resultado["signed_url"]
    except supabase_client.SupabaseStorageError as exc:
        print(f"{exc}. Usando Google Drive como respaldo temporal.")
        link_drive = subir_a_drive(nombre_archivo, pdf_bytes)
        return link_drive, link_drive


def formatear_fecha_resumen(fecha_rfc2822):
    try:
        dt = email.utils.parsedate_to_datetime(fecha_rfc2822)
        return dt.strftime("%d/%m/%Y")
    except Exception:
        return fecha_rfc2822


def guardar_en_sheet(ws, fecha_rfc, asunto, monto_total, fecha_vencimiento, remitente, link_drive=""):
    """FASE 4: escribe en Supabase (persistencia principal).

    NO escribe en Google Sheets (evita doble escritura / inconsistencia). Si
    Supabase falla, propaga SupabaseWriteError ('[SUPABASE WRITE ERROR]') y NO
    inserta en Sheets. Devuelve 'insertado' | 'existente' (el UNIQUE dedup de
    la DB decide: lower(remitente)|vto|monto, igual que es_registro_duplicado).
    """
    return supabase_client.guardar_consolidado(
        formatear_fecha_resumen(fecha_rfc),
        remitente, asunto, monto_total, fecha_vencimiento, link_drive,
    )


def marcar_procesado(mensaje_id, label_id):
    get_gmail_service().users().messages().modify(
        userId="me", id=mensaje_id, body={"addLabelIds": [label_id]}
    ).execute()


def mensaje_tiene_label(mensaje_id, label_id):
    if not label_id:
        return False
    msg = get_gmail_service().users().messages().get(
        userId="me", id=mensaje_id, format="metadata"
    ).execute()
    return label_id in msg.get("labelIds", [])


def mensaje_ya_procesado(mensaje_id, label_id):
    ok, procesado = _leer_con_supabase(
        lambda: supabase_client.mensaje_ya_procesado(mensaje_id),
        f"mensaje procesado Gmail {mensaje_id}",
    )
    if ok:
        return procesado
    return mensaje_tiene_label(mensaje_id, label_id)


def registrar_y_marcar_mensaje_procesado(mensaje_id, remitente, asunto, label_id):
    supabase_client.registrar_mensaje_procesado(mensaje_id, remitente, asunto)
    try:
        marcar_procesado(mensaje_id, label_id)
    except Exception as exc:
        print(
            f"[GMAIL LABEL ERROR] No se pudo etiquetar {mensaje_id}: {exc}. "
            "Supabase ya registró el mensaje como procesado."
        )


# ---------- Normalización Numérica y de Fechas ----------

REGEX_CONSUMO_DEFAULT = r'^(\d{2}\.\d{2}\.\d{2})\s+(?:(\d+)\s+)?(.+?)\s+(-?\d[\d.]*,\d{2})\s+(-?\d[\d.]*,\d{2})\s*$'

MESES_ESPANOL = {
    'ENE': '01', 'FEB': '02', 'MAR': '03', 'ABR': '04',
    'MAY': '05', 'JUN': '06', 'JUL': '07', 'AGO': '08',
    'SEP': '09', 'SET': '09', 'OCT': '10', 'NOV': '11', 'DIC': '12'
}


def normalizar_monto(texto):
    t = str(texto).replace('$', '').strip()
    if '.' in t and ',' in t:
        t = t.replace('.', '').replace(',', '.')
    elif ',' in t:
        t = t.replace(',', '.')
    return round(float(t), 2)


def formatear_fecha_consumo(fecha_str):
    partes = re.split(r'[.\/-]', fecha_str.strip())
    if len(partes) == 3:
        dia, mes, anio = partes
        if len(anio) == 2:
            anio = "20" + anio
        return f"{dia.zfill(2)}/{mes.zfill(2)}/{anio}"
    return fecha_str


def convertir_fecha_texto(dia_str, mes_or_num, anio_str):
    try:
        dia_num = int(dia_str)
        if not (1 <= dia_num <= 31):
            return None

        mes_str = str(mes_or_num).strip().upper()
        if mes_str in MESES_ESPANOL:
            mes_num = int(MESES_ESPANOL[mes_str])
        elif mes_str.isdigit():
            mes_num = int(mes_str)
        else:
            return None

        if not (1 <= mes_num <= 12):
            return None

        anio_full = "20" + anio_str if len(anio_str) == 2 else anio_str
        return f"{str(dia_num).zfill(2)}/{str(mes_num).zfill(2)}/{anio_full}"
    except Exception:
        return None


def es_pago_realizado(detalle_texto):
    detalle_upper = detalle_texto.upper()
    return "SU PAGO" in detalle_upper or "PAGO EN PESOS" in detalle_upper or "PAGO EN DOLARES" in detalle_upper


def extraer_cuotas(detalle_texto, cuota_texto_detectado=None):
    if cuota_texto_detectado:
        c_txt = str(cuota_texto_detectado).strip().upper()
        if c_txt == "ZETA":
            return detalle_texto.strip(), 1, 3
        m = re.match(r'^(\d+)/(\d+)$', c_txt)
        if m:
            return detalle_texto.strip(), int(m.group(1)), int(m.group(2))
        if c_txt.isdigit():
            return detalle_texto.strip(), int(c_txt), int(c_txt)

    m = re.search(r'\s+C\.?\s*(\d+)\s*/\s*(\d+)', detalle_texto, re.IGNORECASE)
    if m:
        cuota_act = int(m.group(1))
        cuota_tot = int(m.group(2))
        detalle_limpio = re.sub(r'\s+C\.?\s*\d+\s*/\s*\d+', '', detalle_texto, flags=re.IGNORECASE).strip()
        return detalle_limpio, cuota_act, cuota_tot
    return detalle_texto.strip(), 1, 1


def es_registro_duplicado(ws_consolidado, remitente, monto_total, fecha_vencimiento):
    ok, existe = _leer_con_supabase(
        lambda: supabase_client.existe_consolidado(
            remitente, monto_total, fecha_vencimiento
        ),
        "duplicado de Consolidado",
    )
    if ok:
        return existe

    # Fallback de LECTURA a Sheets (las escrituras NO tienen fallback en FASE 4).
    try:
        filas = ws_consolidado.get_all_values()
        if len(filas) <= 1:
            return False
            
        m_cand = normalizar_monto(monto_total)
        f_vto_cand = str(fecha_vencimiento).strip()
        r_cand = str(remitente).strip().lower()
        id_buscado = f"{r_cand}|{f_vto_cand}|{m_cand}"
        
        for f in filas[1:]:
            if len(f) >= 7 and f[6].strip():
                if f[6].strip().lower() == id_buscado:
                    return True
            elif len(f) >= 5:
                try:
                    m_sheet = normalizar_monto(f[3]) if f[3] else 0.0
                    f_vto_sheet = str(f[4]).strip()
                    r_sheet = str(f[1]).strip().lower()
                    
                    if r_sheet == r_cand and m_sheet == m_cand and f_vto_sheet == f_vto_cand:
                        return True
                except Exception:
                    continue
    except Exception as e:
        print(f"[DEBUG] Error al verificar duplicados en Consolidado: {str(e)}")
    return False


def buscar_por_tokens(texto_unido, kw_target, es_fecha=False, tomar_segundo=False):
    texto_unido_limpio = re.sub(r'\(?\s*contrato\s*[:\-]?\s*\d*[^\)]*\)?', '', texto_unido, flags=re.IGNORECASE)
    palabras = [p.strip() for p in texto_unido_limpio.split() if p.strip()]
    kw_palabras = [k.strip().lower().rstrip(",:;") for k in kw_target.split() if k.strip()]
    
    if not kw_palabras:
        return None
        
    idx_fin_kw = -1
    len_kw = len(kw_palabras)
    
    for i in range(len(palabras) - len_kw + 1):
        coincide = True
        for j in range(len_kw):
            p_mail = palabras[i + j].rstrip(",:;").lower()
            p_kw = kw_palabras[j]
            if p_mail != p_kw and p_kw not in p_mail:
                coincide = False
                break
        if coincide:
            idx_fin_kw = i + len_kw - 1
            break
            
    if idx_fin_kw == -1:
        return None

    coincidencias_encontradas = 0

    for idx, p_cand in enumerate(palabras[idx_fin_kw + 1 : idx_fin_kw + 20], start=idx_fin_kw + 1):
        p_cand_clean = p_cand.rstrip(",:;")
        
        if es_fecha:
            m = re.match(r'^(\d{1,2})[./-]+([A-Za-z]{3,9}|\d{1,2})[./-]+(\d{2,4})$', p_cand_clean)
            if m:
                f_val = convertir_fecha_texto(m.group(1), m.group(2), m.group(3))
                if f_val:
                    return f_val
        else:
            monto_str = p_cand_clean.replace("$", "").strip()
            if re.match(r'^\d{1,3}(?:\.\d{3})+,\d{2}$', monto_str) or re.match(r'^\d+(?:[.,]\d{1,2})?$', monto_str):
                try:
                    val = normalizar_monto(monto_str)
                    if val >= 10000000.0:
                        continue
                    
                    tiene_decimales = ("," in monto_str) or ("." in monto_str)
                    tiene_signo_pesos = ("$" in p_cand) or (idx > 0 and palabras[idx - 1] == "$")
                    
                    if not tiene_decimales and not tiene_signo_pesos:
                        continue
                        
                    coincidencias_encontradas += 1
                    
                    # REGLA EXCLUSIVA PARA NARANJA: Ignorar el primer monto (Total) y tomar el segundo (Plan Z)
                    if tomar_segundo and coincidencias_encontradas == 1:
                        continue
                        
                    return val
                except Exception:
                    continue
    return None


def identificar_regla_por_pdf(texto_pdf, reglas):
    for r in reglas:
        remitente = r["Remitente"].lower()
        asunto = r.get("Asunto_Contiene", "").lower()
        
        if "bna" in remitente or "visa" in asunto or "mastercard" in asunto:
            if "banco de la nacion" in texto_pdf.lower() or "bna" in texto_pdf.lower():
                return r
        if "epec" in remitente or "epec" in asunto:
            if "epec" in texto_pdf.lower() or "provincia de cordoba" in texto_pdf.lower():
                return r
        if "naranja" in remitente or "naranja" in asunto:
            if "naranja" in texto_pdf.lower():
                return r
        if "metmedicina" in remitente or "met" in remitente or "met" in asunto:
            if "met cordoba" in texto_pdf.lower() or "met medicina" in texto_pdf.lower():
                return r
    return None


def extraer_fechas_y_monto_global(texto_pdf, texto_mail, fecha_mail_fmt, kw_cierre="", kw_vto="", kw_monto="", remitente=""):
    texto_unido = texto_pdf + " " + texto_mail
    
    fecha_cierre = ""
    fecha_vencimiento = ""
    monto_total = 0.0

    kw_cierre_target = kw_cierre.strip() if kw_cierre else "CIERRE ACTUAL"
    fecha_cierre_val = buscar_por_tokens(texto_unido, kw_cierre_target, es_fecha=True)
    if fecha_cierre_val:
        fecha_cierre = fecha_cierre_val

    kw_vto_target = kw_vto.strip() if kw_vto else "VENCIMIENTO"
    fecha_vto_val = buscar_por_tokens(texto_unido, kw_vto_target, es_fecha=True)
    if fecha_vto_val:
        fecha_vencimiento = fecha_vto_val

    if not fecha_cierre:
        fecha_cierre = fecha_mail_fmt
    if not fecha_vencimiento:
        fecha_vencimiento = fecha_cierre

    # Activar la toma del segundo monto exclusivamente si el remitente es Naranja
    es_naranja = "naranja" in str(remitente).lower()
    kw_monto_target = kw_monto.strip() if kw_monto else "SALDO"
    monto_val = buscar_por_tokens(texto_unido, kw_monto_target, es_fecha=False, tomar_segundo=es_naranja)
    if monto_val is not None:
        monto_total = monto_val

    return fecha_cierre, fecha_vencimiento, monto_total


def extraer_consumos_pdf(pdf_bytes, texto_mail, fecha_mail_fmt, regla):
    regex_personalizado = str(regla.get("Regex_Consumo", "")).strip()
    kw_cierre = str(regla.get("Regex_Cierre", "")).strip()
    kw_vto = str(regla.get("Regex_Vencimiento", "")).strip()
    kw_monto = str(regla.get("Regex_Monto", "")).strip()
    remitente = str(regla.get("Remitente", "")).strip()

    patron = re.compile(regex_personalizado or REGEX_CONSUMO_DEFAULT)
    consumos = []
    texto_completo_pdf = ""

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for pagina in pdf.pages:
            texto_completo_pdf += (pagina.extract_text() or "") + "\n"

        fecha_cierre, fecha_vencimiento, monto_total = extraer_fechas_y_monto_global(
            texto_completo_pdf, texto_mail, fecha_mail_fmt, kw_cierre, kw_vto, kw_monto, remitente
        )

        for linea in texto_completo_pdf.split("\n"):
            m = patron.match(linea.strip())
            if m:
                grupos = m.groups()
                if len(grupos) == 6:
                    fecha, tarjeta, comprobante, detalle, cuota_detectada, pesos = grupos
                    detalle_limpio, cuota_actual, cuota_total = extraer_cuotas(detalle, cuota_detectada)
                    pesos_val = normalizar_monto(pesos)
                    dolar_val = 0.0
                    detalle_final = f"{detalle_limpio} ({tarjeta.strip()})"
                elif len(grupos) == 5:
                    g4 = str(grupos[3]).strip()
                    if g4.upper() == "ZETA" or "/" in g4 or (g4.isdigit() and len(g4) == 2 and int(g4) <= 36):
                        fecha, comprobante, detalle, cuota_detectada, pesos = grupos
                        detalle_limpio, cuota_actual, cuota_total = extraer_cuotas(detalle, cuota_detectada)
                        pesos_val = normalizar_monto(pesos)
                        dolar_val = 0.0
                        detalle_final = detalle_limpio
                    else:
                        fecha, comprobante, detalle, pesos, dolar = grupos
                        detalle_limpio, cuota_actual, cuota_total = extraer_cuotas(detalle)
                        pesos_val = normalizar_monto(pesos)
                        dolar_val = normalizar_monto(dolar)
                        detalle_final = detalle_limpio
                elif len(grupos) == 4:
                    fecha, comprobante, detalle, cuota_detectada, pesos = grupos
                    detalle_limpio, cuota_actual, cuota_total = extraer_cuotas(detalle, cuota_detectada)
                    pesos_val = normalizar_monto(pesos)
                    dolar_val = 0.0
                    detalle_final = detalle_limpio
                else:
                    continue

                if es_pago_realizado(detalle_final):
                    continue

                consumos.append({
                    "fecha": formatear_fecha_consumo(fecha),
                    "comprobante": comprobante or "",
                    "detalle": detalle_final,
                    "cuota_actual": cuota_actual,
                    "cuota_total": cuota_total,
                    "pesos": pesos_val,
                    "dolar": dolar_val,
                    "fecha_cierre": fecha_cierre,
                    "fecha_vencimiento": fecha_vencimiento,
                })

    return consumos, fecha_cierre, fecha_vencimiento, monto_total


# ---------- Guardado y Actualización de Consumos ----------

def guardar_o_actualizar_consumos_sheet(ws_consumos, consumos, remitente):
    """FASE 4: escribe en Supabase (persistencia principal).

    NO escribe en Google Sheets (evita doble escritura). Si Supabase falla,
    propaga SupabaseWriteError ('[SUPABASE WRITE ERROR]') y NO inserta en
    Sheets. Devuelve la lista de estados (mismo orden que la entrada):
    'insertado' | 'actualizado' | 'sin_cambios' (cuota nunca retrocede).
    """
    return supabase_client.guardar_o_actualizar_consumos(consumos, remitente)


# ---------- Sincronización Automática de Fijos ----------

def procesar_fijos_mensuales(ws_config, ws_consumos, ws_ingresos):
    if ws_consumos is None:
        return

    ahora = datetime.now(ZoneInfo("America/Argentina/Cordoba"))
    fecha_fijo = f"01/{ahora.strftime('%m/%Y')}"

    gastos_fijos = []
    ingresos_fijos = []

    ok, fijos_supabase = _leer_con_supabase(
        supabase_client.obtener_fijos, "fijos mensuales"
    )
    if ok:
        gastos_fijos, ingresos_fijos = fijos_supabase
    else:
        valores_config = ws_config.get_all_values()
        if len(valores_config) <= 1:
            return

        for fila in valores_config[1:]:
            if len(fila) >= 6:
                tipo_g = str(fila[4]).strip()
                monto_g_raw = str(fila[5]).strip()
                if tipo_g and monto_g_raw:
                    try:
                        val_g = normalizar_monto(monto_g_raw)
                        if val_g > 0:
                            gastos_fijos.append({"tipo": tipo_g, "monto": val_g})
                    except Exception:
                        pass

            if len(fila) >= 10:
                tipo_i = str(fila[8]).strip()
                monto_i_raw = str(fila[9]).strip()
                if tipo_i and monto_i_raw:
                    try:
                        val_i = normalizar_monto(monto_i_raw)
                        if val_i > 0:
                            ingresos_fijos.append({"tipo": tipo_i, "monto": val_i})
                    except Exception:
                        pass

    if gastos_fijos:
        consumos_fijos = [
            {
                "fecha": fecha_fijo, "comprobante": "Fijo Config",
                "detalle": gf["tipo"], "cuota_actual": 1, "cuota_total": 1,
                "pesos": gf["monto"], "dolar": 0.0,
                "fecha_cierre": "", "fecha_vencimiento": "",
            }
            for gf in gastos_fijos
        ]
        resultados_c = supabase_client.guardar_o_actualizar_consumos(
            consumos_fijos, "Fijo Config"
        )
        filas_c_nuevas = [r for r in resultados_c if r["estado"] == "insertado"]

        if filas_c_nuevas:
            msgs = [
                f"📌 Gasto: {r['detalle']} (${r['pesos']:,.2f})"
                for r in filas_c_nuevas
            ]
            enviar_telegram(f"🗓️ Gastos Fijos del Mes ({fecha_fijo}):\n" + "\n".join(msgs))

    if ingresos_fijos and ws_ingresos is not None:
        nuevos_ingresos = []
        for ing in ingresos_fijos:
            estado = supabase_client.guardar_ingreso(
                fecha_fijo, ing["tipo"], ing["monto"], "Fijo Config"
            )
            if estado == "insertado":
                nuevos_ingresos.append(ing)

        if nuevos_ingresos:
            msgs_i = [
                f"💰 Ingreso: {ing['tipo']} (${ing['monto']:,.2f})"
                for ing in nuevos_ingresos
            ]
            enviar_telegram(f"🗓️ Ingresos Fijos del Mes ({fecha_fijo}):\n" + "\n".join(msgs_i))


# ---------- Procesamiento de Telegram ----------

def leer_config_completo(ws_config):
    ok, config_supabase = _leer_con_supabase(
        supabase_client.obtener_config_completo, "config completa"
    )
    if ok:
        # (last_update_id, state, tipos_gastos, tipos_ingresos) 1:1 con la hoja.
        return config_supabase

    valores = ws_config.get_all_values()
    last_update_id = 0
    state = ""
    tipos_gastos = []
    tipos_ingresos = []
    
    if len(valores) > 1:
        if len(valores[1]) > 1 and valores[1][1]:
            try:
                last_update_id = int(valores[1][1])
            except Exception:
                pass
        if len(valores[1]) > 2:
            state = str(valores[1][2]).strip()
            
    for fila in valores[1:]:
        if len(fila) > 4 and fila[4].strip():
            tipos_gastos.append(fila[4].strip())
        if len(fila) > 8 and fila[8].strip():
            tipos_ingresos.append(fila[8].strip())
            
    return last_update_id, state, tipos_gastos, tipos_ingresos


def parsear_monto_manual(texto):
    t_raw = str(texto).strip()
    es_ingreso = t_raw.startswith("+")
    texto_limpio = t_raw.replace("+", "").strip().lower().replace("$", "").replace(" ", "")
    
    factor = 1.0
    if any(kw in texto_limpio for kw in ["millones", "millon", "m"]):
        factor = 1000000.0
        for kw in ["millones", "millon", "m"]:
            texto_limpio = texto_limpio.replace(kw, "")
    elif any(kw in texto_limpio for kw in ["mil", "k"]):
        factor = 1000.0
        for kw in ["mil", "k"]:
            texto_limpio = texto_limpio.replace(kw, "")
        
    m = re.match(r'^(\d+(?:[.,]\d{1,2})?)$', texto_limpio)
    if m:
        try:
            num = normalizar_monto(texto_limpio)
            return round(num * factor, 2), es_ingreso
        except Exception:
            pass
    return None, False


def enviar_teclado_categorias(chat_id, monto, tipos_gastos, tipos_ingresos, es_ingreso=False):
    tipos = tipos_ingresos if es_ingreso else tipos_gastos
    prefix = "INGRESO" if es_ingreso else "MANUAL"
    titulo = "💰 ¿A qué categoría de INGRESO corresponde el monto de" if es_ingreso else "❓ ¿A qué categoría de GASTO corresponde el monto de"

    keyboard = []
    fila = []
    for t in tipos:
        fila.append({"text": t, "callback_data": f"{prefix}|{monto}|{t}"})
        if len(fila) == 2:
            keyboard.append(fila)
            fila = []
    if fila:
        keyboard.append(fila)
        
    reply_markup = {"inline_keyboard": keyboard}
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": f"{titulo} ${monto:,.2f}?",
        "reply_markup": reply_markup
    }
    requests.post(url, json=payload)


def procesar_mensajes_telegram():
    payload_str = os.environ.get("TELEGRAM_UPDATE_PAYLOAD", "").strip()
    
    if not payload_str or payload_str == "null":
        return

    try:
        update = json.loads(payload_str)
        if not update or not isinstance(update, dict):
            return
        updates = [update]
    except Exception as e:
        print(f"[ERROR] No se pudo decodificar el payload de Telegram: {str(e)}")
        return

    sh = get_gc().open_by_key(SHEET_ID)
    ws_config = sh.worksheet("Config")
    _, _, tipos_gastos, tipos_ingresos = leer_config_completo(ws_config)

    for update in updates:
        callback_query = update.get("callback_query")
        if callback_query:
            chat_id = str(callback_query["message"]["chat"]["id"])
            if chat_id == TELEGRAM_CHAT_ID:
                data_seleccionada = callback_query["data"]
                message_id = callback_query["message"]["message_id"]
                
                if data_seleccionada.startswith("MANUAL|"):
                    try:
                        partes = data_seleccionada.split("|")
                        monto_val = float(partes[1])
                        categoria_sel = partes[2]
                        fecha_hoy = datetime.now(ZoneInfo("America/Argentina/Cordoba")).strftime("%d/%m/%Y")
                        
                        supabase_client.guardar_o_actualizar_consumos([{
                            "fecha": fecha_hoy, "comprobante": "Telegram",
                            "detalle": categoria_sel, "cuota_actual": 1,
                            "cuota_total": 1, "pesos": monto_val, "dolar": 0.0,
                            "fecha_cierre": "", "fecha_vencimiento": "",
                        }], "Manual Telegram")
                        
                        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/editMessageText", json={
                            "chat_id": chat_id, "message_id": message_id,
                            "text": f"✅ ¡Gasto de ${monto_val:,.2f} registrado con éxito en '{categoria_sel}'!"
                        })
                    except Exception as e:
                        enviar_telegram(f"❌ Error al registrar gasto manual: {str(e)}")

                elif data_seleccionada.startswith("INGRESO|"):
                    try:
                        partes = data_seleccionada.split("|")
                        monto_val = float(partes[1])
                        categoria_sel = partes[2]
                        fecha_hoy = datetime.now(ZoneInfo("America/Argentina/Cordoba")).strftime("%d/%m/%Y")
                        
                        supabase_client.guardar_ingreso(
                            fecha_hoy, categoria_sel, monto_val, "Manual Telegram"
                        )
                        
                        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/editMessageText", json={
                            "chat_id": chat_id, "message_id": message_id,
                            "text": f"💰 ¡Ingreso de ${monto_val:,.2f} registrado con éxito en '{categoria_sel}'!"
                        })
                    except Exception as e:
                        enviar_telegram(f"❌ Error al registrar ingreso manual: {str(e)}")
            continue

        message = update.get("message")
        if not message:
            continue
            
        chat_id = str(message["chat"]["id"])
        if chat_id != TELEGRAM_CHAT_ID:
            continue

        texto = message.get("text")
        if texto:
            monto_detectado, es_ingreso = parsear_monto_manual(texto)
            if monto_detectado is not None:
                enviar_teclado_categorias(chat_id, monto_detectado, tipos_gastos, tipos_ingresos, es_ingreso)
                continue

        document = message.get("document")
        if document and document.get("mime_type") == "application/pdf":
            file_id = document["file_id"]
            file_name = document.get("file_name", "Factura.pdf")
            
            try:
                get_file_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile?file_id={file_id}"
                file_path = requests.get(get_file_url).json()["result"]["file_path"]
                download_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
                pdf_bytes = requests.get(download_url).content
                
                texto_completo_pdf = ""
                with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                    for pagina in pdf.pages:
                        texto_completo_pdf += (pagina.extract_text() or "") + "\n"
                
                reglas = obtener_reglas()
                regla = identificar_regla_por_pdf(texto_completo_pdf, reglas)
                if not regla:
                    enviar_telegram("❌ Error: No logré identificar a qué empresa pertenece esta factura. Verifica que la regla esté activa en la hoja Datos.")
                    continue
                
                remitente = regla["Remitente"]
                clave = regla.get("Clave", "")
                es_tarjeta = regla.get("Es_Tarjeta_Credito", "NO") == "SI"
                
                pdf_sin_clave = quitar_clave_pdf(pdf_bytes, clave) if clave else pdf_bytes
                
                fecha_mail_fmt = datetime.now(ZoneInfo("America/Argentina/Cordoba")).strftime("%d/%m/%Y")
                consumos, fecha_cierre, fecha_vencimiento, monto_total = extraer_consumos_pdf(
                    pdf_sin_clave, "", fecha_mail_fmt, regla
                )
                
                ws_consolidado = sh.worksheet(SHEET_NAME)
                if es_registro_duplicado(ws_consolidado, remitente, monto_total, fecha_vencimiento):
                    enviar_telegram(f"⚠️ El archivo enviado de {remitente} ya fue procesado anteriormente (Monto: ${monto_total:,.2f}, Vto: {fecha_vencimiento}).")
                    continue

                link_guardado = ""
                link_telegram = ""
                if "epec.com.ar" not in remitente.lower():
                    link_guardado, link_telegram = subir_pdf(
                        file_name, pdf_sin_clave, remitente
                    )

                if es_tarjeta and consumos:
                    ws_consumos = sh.worksheet("Consumos")
                    guardar_o_actualizar_consumos_sheet(ws_consumos, consumos, remitente)
                    
                guardar_en_sheet(ws_consolidado, datetime.now().strftime("%a, %d %b %Y %H:%M:%S -0000"), f"Resumen recibido por Telegram ({file_name})", monto_total, fecha_vencimiento, remitente, link_guardado)
                
                confirmacion = f"✅ ¡Factura procesada con éxito desde Telegram!\n🏢 Empresa: {remitente}\n💵 Monto: ${monto_total:,.2f}\n📅 Vencimiento: {fecha_vencimiento}"
                if link_telegram:
                    confirmacion += f"\n📂 Archivo: {link_telegram}"
                enviar_telegram(confirmacion)
                
            except Exception as e:
                enviar_telegram(f"❌ Ocurrió un error al procesar tu archivo '{file_name}': {str(e)}")


# ---------- Flujo de Trabajo Principal ----------

def revisar_mails():
    print("\n" + "="*60)
    print("[INICIO] revisar_mails()")
    print("="*60)
    label_id = obtener_o_crear_label(LABEL_PROCESADO)
    reglas = obtener_reglas()
    total_procesados = 0
    excluir_label_en_busqueda = not supabase_client.supabase_disponible()

    sh = get_gc().open_by_key(SHEET_ID)
    ws_consolidado = sh.worksheet(SHEET_NAME)
    ws_config = sh.worksheet("Config")
    
    try:
        ws_consumos = sh.worksheet("Consumos")
    except gspread.WorksheetNotFound:
        ws_consumos = None

    try:
        ws_ingresos = sh.worksheet("Ingresos")
    except gspread.WorksheetNotFound:
        ws_ingresos = None

    # Sincronización automática de gastos e ingresos fijos del mes
    try:
        procesar_fijos_mensuales(ws_config, ws_consumos, ws_ingresos)
    except Exception as e_fijos:
        print(f"[ERROR] Error al procesar fijos mensuales: {str(e_fijos)}")

    print(f"[DEBUG] Procesando {len(reglas)} reglas de Gmail...\n")
    for idx, regla in enumerate(reglas, 1):
        try:
            remitente = regla["Remitente"]
            asunto_contiene = regla.get("Asunto_Contiene", "")
            clave = str(regla.get("Clave", "")).strip()
            tiene_adjunto = str(regla.get("Tiene_Adjunto", "NO")).strip().upper() == "SI"
            es_tarjeta = str(regla.get("Es_Tarjeta_Credito", "NO")).strip().upper() == "SI"

            nuevos = buscar_mails_nuevos(
                remitente,
                asunto_contiene,
                excluir_label_procesado=excluir_label_en_busqueda,
            )

            if nuevos:
                nuevos.reverse()

            for m in nuevos:
                try:
                    if mensaje_ya_procesado(m["id"], label_id):
                        continue

                    asunto, fecha, cuerpo_texto, cuerpo_raw, pdf_filename, pdf_bytes = extraer_datos_mensaje_mime(m["id"])
                    link_guardado = ""
                    link_telegram = ""
                    fecha_mail_fmt = formatear_fecha_resumen(fecha)
                    monto_total = 0.0
                    fecha_vencimiento = ""
                    pdf_sin_clave = None

                    if tiene_adjunto:
                        if not pdf_bytes:
                            pdf_filename, pdf_bytes = descargar_pdf_desde_link(cuerpo_raw)

                        if pdf_bytes:
                            try:
                                pdf_sin_clave = quitar_clave_pdf(pdf_bytes, clave) if clave else pdf_bytes

                                consumos, _, fecha_vencimiento, monto_total = extraer_consumos_pdf(
                                    pdf_sin_clave, cuerpo_texto, fecha_mail_fmt, regla
                                )
                                
                                if ws_consumos is not None and es_tarjeta and consumos:
                                    guardar_o_actualizar_consumos_sheet(ws_consumos, consumos, remitente)
                            except pikepdf.PasswordError:
                                print("[ERROR] Clave de PDF incorrecta")

                    if not fecha_vencimiento or monto_total == 0.0:
                        if not tiene_adjunto:
                            kw_cierre = str(regla.get("Regex_Cierre", "")).strip()
                            kw_vto = str(regla.get("Regex_Vencimiento", "")).strip()
                            kw_monto = str(regla.get("Regex_Monto", "")).strip()
                            _, fecha_vencimiento, monto_total = extraer_fechas_y_monto_global("", cuerpo_texto, fecha_mail_fmt, kw_cierre, kw_vto, kw_monto, remitente)

                    if es_registro_duplicado(ws_consolidado, remitente, monto_total, fecha_vencimiento):
                        registrar_y_marcar_mensaje_procesado(
                            m["id"], remitente, asunto, label_id
                        )
                        continue

                    if pdf_sin_clave and "epec.com.ar" not in remitente.lower():
                        link_guardado, link_telegram = subir_pdf(
                            pdf_filename or "Factura.pdf", pdf_sin_clave, remitente
                        )

                    guardar_en_sheet(ws_consolidado, fecha, asunto, monto_total, fecha_vencimiento, remitente, link_guardado)
                    
                    texto_telegram = f"📩 Resumen Procesado\nDe: {remitente}\nAsunto: {asunto}\nMonto: ${monto_total:,.2f}\nVencimiento: {fecha_vencimiento}"
                    if link_telegram:
                        texto_telegram += f"\nPDF: {link_telegram}"
                    enviar_telegram(texto_telegram)
                    
                    registrar_y_marcar_mensaje_procesado(
                        m["id"], remitente, asunto, label_id
                    )
                    total_procesados += 1
                    time.sleep(1)
                    
                except Exception as msg_error:
                    error_detalle = f"❌ Error procesando correo ID {m.get('id', 'desconocido')} de {remitente}: {str(msg_error)}"
                    print(f"[ERROR] {error_detalle}")
                    enviar_telegram(error_detalle)
                    continue
                    
        except Exception as rule_error:
            error_detalle = f"❌ Error procesando Regla {idx} ({regla.get('Remitente', 'desconocido')}): {str(rule_error)}"
            print(f"[ERROR] {error_detalle}")
            enviar_telegram(error_detalle)
            continue

    print(f"[RESULTADO] {total_procesados} mail(s) procesado(s).")


if __name__ == "__main__":
    print("[MAIN] Iniciando ejecución principal...")
    try:
        payload_telegram = os.environ.get("TELEGRAM_UPDATE_PAYLOAD", "").strip()
        tiene_payload_valido = payload_telegram and payload_telegram != "null"
        
        if tiene_payload_valido:
            print("[MAIN] Ejecución exclusiva de Telegram (Ruta ultra-rápida)...")
            procesar_mensajes_telegram()
        elif debe_ejecutar_ahora():
            print("[MAIN] Ejecutando revisión completa de mails...")
            revisar_mails()
        else:
            print("[MAIN] Todavía no es la hora configurada en el Sheet y no hay payload de Telegram.")
            
    except Exception as e:
        print(f"[MAIN] ERROR durante la ejecución: {str(e)}")
        import traceback as _tb
        print(f"[MAIN] Traceback: {_tb.format_exc()}")
        raise
