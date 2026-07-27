import os
import re
import io
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

print("[INICIO] Inicializando bot_Saldo.py con Algoritmo de Tokenización...")

try:
    # ---------- Configuración desde variables de entorno (secrets) ----------
    TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
    TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
    SHEET_ID = os.environ["SHEET_ID"]
    SHEET_NAME = "Consolidado"

    print(f"[DEBUG] SHEET_ID: {SHEET_ID[:20]}...")
    
    creds = Credentials(
        token=None,
        refresh_token=os.environ["GOOGLE_REFRESH_TOKEN"],
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=[
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive.file",
        ],
    )
    print("[DEBUG] Credenciales de Google creadas")

    gmail_service = build("gmail", "v1", credentials=creds)
    print("[DEBUG] Gmail service inicializado")
    drive_service = build("drive", "v3", credentials=creds)
    print("[DEBUG] Drive service inicializado")
    gc = gspread.authorize(creds)
    print("[DEBUG] Gspread client inicializado")
    
except Exception as e:
    print(f"[ERROR] Fallo durante la inicialización: {str(e)}")
    print(f"[ERROR] Traceback: {traceback.format_exc()}")
    raise

LABEL_PROCESADO = "Procesado-Resumen"


def debe_ejecutar_ahora():
    forzar = os.environ.get("FORZAR_EJECUCION", "false").lower()
    print(f"[DEBUG] FORZAR_EJECUCION env var: '{forzar}'")
    if forzar == "true":
        print("[DEBUG] ✅ Ejecución FORZADA - ignorando hora del Config sheet")
        return True

    print("[DEBUG] Verificando hora del Config sheet...")
    sh = gc.open_by_key(SHEET_ID)
    ws_config = sh.worksheet("Config")
    filas = ws_config.get_all_records()
    if not filas:
        print("[DEBUG] No hay configuración en Config sheet, ejecutando...")
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
    labels = gmail_service.users().labels().list(userId="me").execute().get("labels", [])
    for l in labels:
        if l["name"] == nombre:
            return l["id"]
    nuevo = gmail_service.users().labels().create(
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
    print("[DEBUG] Leyendo reglas de la hoja Datos...")
    sh = gc.open_by_key(SHEET_ID)
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
                "Regex_Consumo": obtener_valor_clave_flexible(f, "Regex_Consumo"),
                "Regex_Cierre": obtener_valor_clave_flexible(f, "Regex_Cierre"),
                "Regex_Vencimiento": obtener_valor_clave_flexible(f, "Regex_Vencimiento"),
                "Regex_Monto": obtener_valor_clave_flexible(f, "Regex_Monto"),
            }
            reglas_activas.append(regla_norm)

    print(f"[DEBUG] Reglas activas encontradas: {len(reglas_activas)}")
    for i, r in enumerate(reglas_activas, 1):
        print(f"  Regla {i}: Remitente={r.get('Remitente')}, Asunto={r.get('Asunto_Contiene', '')}, Tiene_Adjunto={r.get('Tiene_Adjunto')}")
    return reglas_activas


def buscar_mails_nuevos(remitente, asunto_contiene):
    query = f"from:{remitente} -label:{LABEL_PROCESADO}"
    if asunto_contiene:
        query += f' subject:"{asunto_contiene}"'
    print(f"[DEBUG] Buscando mails con query: {query}")
    resultados = gmail_service.users().messages().list(userId="me", q=query).execute()
    mails_encontrados = resultados.get("messages", [])
    print(f"[DEBUG] Mails encontrados: {len(mails_encontrados)}")
    return mails_encontrados


def limpiar_html(texto_html):
    """Comprime todo el correo en una sola línea continua de lectura para el bot."""
    texto = re.sub(r"<[^>]+>", " ", texto_html)
    texto = re.sub(r"&nbsp;|&zwnj;", " ", texto)
    # Reemplazar todos los espacios (incluidos saltos de línea) por un solo espacio horizontal
    texto = re.sub(r"\s+", " ", texto)
    return texto.strip()


def extraer_datos_mensaje_mime(mensaje_id):
    msg = gmail_service.users().messages().get(userId="me", id=mensaje_id, format="raw").execute()
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
        
        # Detectar adjunto PDF
        if "attachment" in disposition and part.get_filename() and part.get_filename().lower().endswith(".pdf"):
            pdf_filename = part.get_filename()
            pdf_bytes = part.get_payload(decode=True)
        elif part.get_filename() and part.get_filename().lower().endswith(".pdf"):
            pdf_filename = part.get_filename()
            pdf_bytes = part.get_payload(decode=True)
            
        # Extraer cuerpos de texto
        if content_type == "text/html":
            cuerpo_html = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="ignore")
        elif content_type == "text/plain":
            cuerpo_plain = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="ignore")
            
    cuerpo_raw = cuerpo_html or cuerpo_plain or ""
    cuerpo_texto = limpiar_html(cuerpo_raw)
    
    return asunto, fecha, cuerpo_texto, cuerpo_raw, pdf_filename, pdf_bytes


def descargar_pdf_desde_link(cuerpo_raw):
    """Descarga de PDF por enlaces usando firma de navegador real para evitar bloqueos."""
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
                print(f"[DEBUG] ✅ PDF descargado exitosamente desde enlace: {url_limpia[:60]}...")
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
    archivo = drive_service.files().create(
        body={"name": nombre_archivo}, media_body=media, fields="id, webViewLink"
    ).execute()
    drive_service.permissions().create(
        fileId=archivo["id"], body={"role": "reader", "type": "anyone"}
    ).execute()
    return archivo.get("webViewLink")


def formatear_fecha_resumen(fecha_rfc2822):
    try:
        dt = email.utils.parsedate_to_datetime(fecha_rfc2822)
        return dt.strftime("%d/%m/%Y")
    except Exception:
        return fecha_rfc2822


def guardar_en_sheet(ws, fecha_rfc, asunto, monto_total, fecha_vencimiento, remitente, link_drive=""):
    ws.append_row([
        formatear_fecha_resumen(fecha_rfc),
        remitente,
        asunto,
        monto_total,
        fecha_vencimiento,
        link_drive
    ], value_input_option="USER_ENTERED")


def marcar_procesado(mensaje_id, label_id):
    gmail_service.users().messages().modify(
        userId="me", id=mensaje_id, body={"addLabelIds": [label_id]}
    ).execute()


# ---------- Normalización Numérica y de Fechas ----------

REGEX_CONSUMO_DEFAULT = r'^(\d{2}\.\d{2}\.\d{2})\s+(?:(\d+)\s+)?(.+?)\s+(-?\d[\d.]*,\d{2})\s+(-?\d[\d.]*,\d{2})\s*$'

MESES_ESPANOL = {
    'ENE': '01', 'FEB': '02', 'MAR': '03', 'ABR': '04',
    'MAY': '05', 'JUN': '06', 'JUL': '07', 'AGO': '08',
    'SEP': '09', 'SET': '09', 'OCT': '10', 'NOV': '11', 'DIC': '12'
}


def normalizar_monto(texto):
    """Soporta montos con coma (14439,4), punto (17161.6) y formato argentino (331.244,18)."""
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
    """Validación estricta de calendario (1-31) y (1-12) para descartar números de documento o contrato."""
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


def extraer_cuotas(detalle_texto):
    m = re.search(r'\s+C\.?\s*(\d+)\s*/\s*(\d+)', detalle_texto, re.IGNORECASE)
    if m:
        cuota_act = int(m.group(1))
        cuota_tot = int(m.group(2))
        detalle_limpio = re.sub(r'\s+C\.?\s*\d+\s*/\s*\d+', '', detalle_texto, flags=re.IGNORECASE).strip()
        return detalle_limpio, cuota_act, cuota_tot
    return detalle_texto.strip(), 1, 1


def buscar_por_tokens(texto_unido, kw_target, es_fecha=False):
    """Algoritmo de cajones/palabras (Tokenización) para escaneo absoluto de izquierda a derecha."""
    # Eliminar quirúrgicamente el bloque de contrato para evitar interferencias
    texto_unido_limpio = re.sub(r'\(?\s*contrato\s*[:\-]?\s*\d*[^\)]*\)?', '', texto_unido, flags=re.IGNORECASE)
    
    # Dividir el texto en una lista de palabras/cajones individuales
    palabras = [p.strip() for p in texto_unido_limpio.split(" ") if p.strip()]
    
    # Encontrar el cajón/índice de la palabra clave
    idx_kw = -1
    for i, p in enumerate(palabras):
        if p.lower() == kw_target.lower() or kw_target.lower() in p.lower():
            idx_kw = i
            break
            
    if idx_kw == -1:
        return None

    # Escanear los cajones hacia la derecha de uno en uno (revisa hasta 20 palabras a la derecha)
    for p_cand in palabras[idx_kw + 1 : idx_kw + 20]:
        p_cand_clean = p_cand.rstrip(",:;")
        
        if es_fecha:
            # Validar patrón de fecha
            m = re.match(r'^(\d{1,2})[./-]+([A-Za-z]{3,9}|\d{1,2})[./-]+(\d{2,4})$', p_cand_clean)
            if m:
                f_val = convertir_fecha_texto(m.group(1), m.group(2), m.group(3))
                if f_val:
                    return f_val
        else:
            # Validar patrón de dinero (con signo $, decimales opcionales, y límite de tamaño)
            monto_str = p_cand_clean.replace("$", "").strip()
            if re.match(r'^\d{1,3}(?:\.\d{3})+,\d{2}$', monto_str) or re.match(r'^\d+(?:[.,]\d{1,2})?$', monto_str):
                try:
                    val = normalizar_monto(monto_str)
                    # Filtro de seguridad: descarta números gigantes de documentos/CUITs
                    if val < 10000000.0:
                        return val
                except Exception:
                    continue
    return None


def extraer_fechas_y_monto_global(texto_pdf, texto_mail, fecha_mail_fmt, kw_cierre="", kw_vto="", kw_monto=""):
    """Extrae datos basándose en el algoritmo de Tokenización (Cajones)."""
    texto_unido = texto_pdf + " " + texto_mail
    
    fecha_cierre = ""
    fecha_vencimiento = ""
    monto_total = 0.0

    # 1. Extracción de Fecha Cierre (Escaneo por tokens)
    kw_cierre_target = kw_cierre.strip() if kw_cierre else "CIERRE ACTUAL"
    fecha_cierre_val = buscar_por_tokens(texto_unido, kw_cierre_target, es_fecha=True)
    if fecha_cierre_val:
        fecha_cierre = fecha_cierre_val

    # 2. Extracción de Fecha Vencimiento (Escaneo por tokens)
    kw_vto_target = kw_vto.strip() if kw_vto else "VENCIMIENTO"
    fecha_vto_val = buscar_por_tokens(texto_unido, kw_vto_target, es_fecha=True)
    if fecha_vto_val:
        fecha_vencimiento = fecha_vto_val

    # Fallbacks de respaldo
    if not fecha_cierre:
        fecha_cierre = fecha_mail_fmt
    if not fecha_vencimiento:
        fecha_vencimiento = fecha_cierre

    # 3. Extracción del Monto Total (Escaneo por tokens)
    kw_monto_target = kw_monto.strip() if kw_monto else "SALDO"
    monto_val = buscar_por_tokens(texto_unido, kw_monto_target, es_fecha=False)
    if monto_val is not None:
        monto_total = monto_val

    return fecha_cierre, fecha_vencimiento, monto_total


def extraer_consumos_pdf(pdf_bytes, texto_mail, fecha_mail_fmt, regla):
    regex_personalizado = str(regla.get("Regex_Consumo", "")).strip()
    kw_cierre = str(regla.get("Regex_Cierre", "")).strip()
    kw_vto = str(regla.get("Regex_Vencimiento", "")).strip()
    kw_monto = str(regla.get("Regex_Monto", "")).strip()

    patron = re.compile(regex_personalizado or REGEX_CONSUMO_DEFAULT)
    consumos = []
    texto_completo_pdf = ""

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for pagina in pdf.pages:
            texto_completo_pdf += (pagina.extract_text() or "") + "\n"

        fecha_cierre, fecha_vencimiento, monto_total = extraer_fechas_y_monto_global(
            texto_completo_pdf, texto_mail, fecha_mail_fmt, kw_cierre, kw_vto, kw_monto
        )

        for linea in texto_completo_pdf.split("\n"):
            m = patron.match(linea.strip())
            if m:
                fecha, comprobante, detalle, pesos, dolar = m.groups()
                
                if es_pago_realizado(detalle):
                    continue
                
                fecha_formateada = formatear_fecha_consumo(fecha)
                detalle_limpio, cuota_actual, cuota_total = extraer_cuotas(detalle)
                
                consumos.append({
                    "fecha": fecha_formateada,
                    "comprobante": comprobante or "",
                    "detalle": detalle_limpio,
                    "cuota_actual": cuota_actual,
                    "cuota_total": cuota_total,
                    "pesos": normalizar_monto(pesos),
                    "dolar": normalizar_monto(dolar),
                    "fecha_cierre": fecha_cierre,
                    "fecha_vencimiento": fecha_vencimiento,
                })

    return consumos, fecha_cierre, fecha_vencimiento, monto_total


def guardar_consumos_sheet(ws_consumos, consumos, remitente):
    if not consumos:
        return
    filas = [
        [
            c["fecha"],
            c["comprobante"],
            c["detalle"],
            c["cuota_actual"],
            c["cuota_total"],
            c["pesos"],
            c["dolar"],
            c["fecha_cierre"],
            c["fecha_vencimiento"],
            remitente
        ]
        for c in consumos
    ]
    ws_consumos.append_rows(filas, value_input_option="USER_ENTERED")


def revisar_mails():
    print("\n" + "="*60)
    print("[INICIO] revisar_mails()")
    print("="*60)
    label_id = obtener_o_crear_label(LABEL_PROCESADO)
    print(f"[DEBUG] Label ID: {label_id}")
    reglas = obtener_reglas()
    total_procesados = 0

    print("[DEBUG] Abriendo hojas de Sheets (una sola vez)...")
    sh = gc.open_by_key(SHEET_ID)
    ws_consolidado = sh.worksheet(SHEET_NAME)
    try:
        ws_consumos = sh.worksheet("Consumos")
    except gspread.WorksheetNotFound:
        print("[DEBUG] Hoja 'Consumos' no existe, se omitirá el guardado de consumos.")
        ws_consumos = None
    print("[DEBUG] Hojas abiertas correctamente.")

    print(f"[DEBUG] Procesando {len(reglas)} reglas...\n")
    for idx, regla in enumerate(reglas, 1):
        print(f"\n--- Regla {idx} ---")
        remitente = regla["Remitente"]
        asunto_contiene = regla.get("Asunto_Contiene", "")
        clave = str(regla.get("Clave", "")).strip()
        tiene_adjunto = str(regla.get("Tiene_Adjunto", "NO")).strip().upper() == "SI"
        
        print(f"[DEBUG] Remitente: {remitente}")
        print(f"[DEBUG] Asunto contiene: '{asunto_contiene}'")
        print(f"[DEBUG] Tiene Adjunto: {'SI' if tiene_adjunto else 'NO'}")

        nuevos = buscar_mails_nuevos(remitente, asunto_contiene)

        for m in nuevos:
            asunto, fecha, cuerpo_texto, cuerpo_raw, pdf_filename, pdf_bytes = extraer_datos_mensaje_mime(m["id"])
            link_drive = ""
            fecha_mail_fmt = formatear_fecha_resumen(fecha)
            monto_total = 0.0
            fecha_vencimiento = ""

            # CASO A: Regla configurada con Tiene_Adjunto = SI
            if tiene_adjunto:
                # 1. Intentar adjunto físico tradicional en el mail
                if not pdf_bytes:
                    pdf_filename, pdf_bytes = descargar_pdf_desde_link(cuerpo_raw)

                if pdf_bytes:
                    try:
                        pdf_sin_clave = quitar_clave_pdf(pdf_bytes, clave) if clave else pdf_bytes
                        
                        # Solo subir a Google Drive si NO es EPEC (para mantener a EPEC puramente temporal)
                        if "epec.com.ar" not in remitente.lower():
                            link_drive = subir_a_drive(pdf_filename or "Factura.pdf", pdf_sin_clave)
                            print(f"[DEBUG] Archivo subido exitosamente a Google Drive: {link_drive}")
                        else:
                            print("[DEBUG] Remitente es EPEC, se procesa temporalmente en memoria sin subir a Google Drive.")
                            link_drive = ""

                        consumos, _, fecha_vencimiento, monto_total = extraer_consumos_pdf(
                            pdf_sin_clave, cuerpo_texto, fecha_mail_fmt, regla
                        )
                        
                        if ws_consumos is not None and consumos:
                            guardar_consumos_sheet(ws_consumos, consumos, remitente)
                    except pikepdf.PasswordError:
                        print("[ERROR] Clave de PDF incorrecta")

            # CASO B: Si Tiene_Adjunto es NO o si la lectura del PDF falló / no encontró datos
            if not fecha_vencimiento or monto_total == 0.0:
                # Solo ejecutamos el fallback si Tiene_Adjunto es NO para evitar mezclas
                if not tiene_adjunto:
                    kw_cierre = str(regla.get("Regex_Cierre", "")).strip()
                    kw_vto = str(regla.get("Regex_Vencimiento", "")).strip()
                    kw_monto = str(regla.get("Regex_Monto", "")).strip()
                    _, fecha_vencimiento, monto_total = extraer_fechas_y_monto_global("", cuerpo_texto, fecha_mail_fmt, kw_cierre, kw_vto, kw_monto)

            guardar_en_sheet(ws_consolidado, fecha, asunto, monto_total, fecha_vencimiento, remitente, link_drive)
            
            texto_telegram = f"📩 Resumen Procesado\nDe: {remitente}\nAsunto: {asunto}\nMonto: ${monto_total:,.2f}\nVencimiento: {fecha_vencimiento}"
            if link_drive:
                texto_telegram += f"\nPDF: {link_drive}"
            enviar_telegram(texto_telegram)
            
            marcar_procesado(m["id"], label_id)
            total_procesados += 1

    print(f"\n" + "="*60)
    print(f"[RESULTADO] {total_procesados} mail(s) procesado(s).")
    print("="*60 + "\n")


if __name__ == "__main__":
    print("[MAIN] Iniciando ejecución principal...")
    try:
        if debe_ejecutar_ahora():
            print("[MAIN] Ejecutando revisar_mails()...")
            revisar_mails()
        else:
            print("[MAIN] Todavía no es la hora configurada en el Sheet.")
    except Exception as e:
        print(f"[MAIN] ERROR durante la ejecución: {str(e)}")
        import traceback as _tb
        print(f"[MAIN] Traceback: {_tb.format_exc()}")
        raise
