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

print("[INICIO] Inicializando bot_Saldo.py con Inteligencia Conversacional...")

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
                "Es_Tarjeta_Credito": obtener_valor_clave_flexible(f, "Es_Tarjeta_Credito").upper() or "NO",
                "Regex_Consumo": obtener_valor_clave_flexible(f, "Regex_Consumo"),
                "Regex_Cierre": obtener_valor_clave_flexible(f, "Regex_Cierre"),
                "Regex_Vencimiento": obtener_valor_clave_flexible(f, "Regex_Vencimiento"),
                "Regex_Monto": obtener_valor_clave_flexible(f, "Regex_Monto"),
            }
            reglas_activas.append(regla_norm)

    print(f"[DEBUG] Reglas activas encontradas: {len(reglas_activas)}")
    for i, r in enumerate(reglas_activas, 1):
        print(f"  Regla {i}: Remitente={r.get('Remitente')}, Asunto={r.get('Asunto_Contiene', '')}, Tiene_Adjunto={r.get('Tiene_Adjunto')}, Tarjeta={r.get('Es_Tarjeta_Credito')}")
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
    texto = re.sub(r"<[^>]+>", " ", texto_html)
    texto = re.sub(r"&nbsp;|&zwnj;", " ", texto)
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


def extraer_cuotas(detalle_texto, cuota_texto_detectado=None):
    """Traductor automático de Plan Zeta a 1/3 y cuotas estándar."""
    if cuota_texto_detectado:
        c_txt = str(cuota_texto_detectado).strip().upper()
        if c_txt == "ZETA":
            return detalle_texto.strip(), 1, 3
        m = re.match(r'^(\d+)/(\d+)$', c_txt)
        if m:
            return detalle_texto.strip(), int(m.group(1)), int(m.group(2))
        if c_txt.isdigit():
            return detalle_texto.strip(), int(c_txt), int(c_txt)

    # Fallback/Default para BNA Visa
    m = re.search(r'\s+C\.?\s*(\d+)\s*/\s*(\d+)', detalle_texto, re.IGNORECASE)
    if m:
        cuota_act = int(m.group(1))
        cuota_tot = int(m.group(2))
        detalle_limpio = re.sub(r'\s+C\.?\s*\d+\s*/\s*\d+', '', detalle_texto, flags=re.IGNORECASE).strip()
        return detalle_limpio, cuota_act, cuota_tot
    return detalle_texto.strip(), 1, 1


def es_registro_duplicado(ws_consolidado, remitente, monto_total, fecha_vencimiento):
    """Evita duplicados: valida si ya existe el mismo remitente, monto y vencimiento en Consolidado."""
    try:
        filas = ws_consolidado.get_all_values()
        if len(filas) <= 1:
            return False
            
        m_cand = normalizar_monto(monto_total)
        f_vto_cand = str(fecha_vencimiento).strip()
        r_cand = str(remitente).strip().lower()
        
        # Fila: [Fecha Mail, Remitente, Asunto, Monto Total, Fecha Vencimiento, Link Drive]
        for f in filas[1:]:
            if len(f) >= 5:
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


def buscar_por_tokens(texto_unido, kw_target, es_fecha=False):
    """Algoritmo de cajones/palabras (Tokenización) con coincidencia de secuencia de palabras (multi-cajón)."""
    # Eliminar quirúrgicamente el bloque de contrato para evitar interferencias
    texto_unido_limpio = re.sub(r'\(?\s*contrato\s*[:\-]?\s*\d*[^\)]*\)?', '', texto_unido, flags=re.IGNORECASE)
    
    # Separar texto del correo por espacios (cajones)
    palabras = [p.strip() for p in texto_unido_limpio.split(" ") if p.strip()]
    
    # Separar la palabra clave de Datos en cajones individuales (para soportar frases como "Total a pagar")
    kw_palabras = [k.strip().lower() for k in kw_target.split(" ") if k.strip()]
    if not kw_palabras:
        return None
        
    idx_fin_kw = -1
    len_kw = len(kw_palabras)
    
    # Deslizar ventana para encontrar la secuencia exacta de cajones de la palabra clave
    for i in range(len(palabras) - len_kw + 1):
        coincide = True
        for j in range(len_kw):
            p_mail = palabras[i + j].rstrip(",:;").lower()
            p_kw = kw_palabras[j]
            # Coincidencia parcial o exacta
            if p_mail != p_kw and p_kw not in p_mail:
                coincide = False
                break
        if coincide:
            idx_fin_kw = i + len_kw - 1  # Guardamos el índice del último cajón coincidente
            break
            
    if idx_fin_kw == -1:
        return None

    # Escanear los cajones inmediatamente posteriores al bloque de la palabra clave (hasta 20 cajones)
    for idx, p_cand in enumerate(palabras[idx_fin_kw + 1 : idx_fin_kw + 20], start=idx_fin_kw + 1):
        p_cand_clean = p_cand.rstrip(",:;")
        
        if es_fecha:
            m = re.match(r'^(\d{1,2})[./-]+([A-Za-z]{3,9}|\d{1,2})[./-]+(\d{2,4})$', p_cand_clean)
            if m:
                f_val = convertir_fecha_texto(m.group(1), m.group(2), m.group(3))
                if f_val:
                    return f_val
        else:
            # --- Regla de Coherencia de Dinero (Evita confundirse con números de cuenta o documento) ---
            monto_str = p_cand_clean.replace("$", "").strip()
            
            # Verificamos si cumple con el formato básico de número entero o decimal
            if re.match(r'^\d{1,3}(?:\.\d{3})+,\d{2}$', monto_str) or re.match(r'^\d+(?:[.,]\d{1,2})?$', monto_str):
                try:
                    val = normalizar_monto(monto_str)
                    
                    # Filtro 1: No debe ser un número gigante de documento, medidor o CUIT
                    if val >= 10000000.0:
                        continue
                    
                    # Filtro 2: Si es un entero puro sin decimales (ej: 0006):
                    # Exigimos obligatoriamente que contenga "$" o que el cajón anterior en la lista sea "$"
                    tiene_decimales = ("," in monto_str) or ("." in monto_str)
                    tiene_signo_pesos = ("$" in p_cand) or (idx > 0 and palabras[idx - 1] == "$")
                    
                    if not tiene_decimales and not tiene_signo_pesos:
                        continue
                        
                    return val
                except Exception:
                    continue
    return None


def extraer_fechas_y_monto_global(texto_pdf, texto_mail, fecha_mail_fmt, kw_cierre="", kw_vto="", kw_monto=""):
    texto_unido = texto_pdf + " " + texto_mail
    
    fecha_cierre = ""
    fecha_vencimiento = ""
    monto_total = 0.0

    # 1. Extracción de Fecha Cierre (Tokens)
    kw_cierre_target = kw_cierre.strip() if kw_cierre else "CIERRE ACTUAL"
    fecha_cierre_val = buscar_por_tokens(texto_unido, kw_cierre_target, es_fecha=True)
    if fecha_cierre_val:
        fecha_cierre = fecha_cierre_val

    # 2. Extracción de Fecha Vencimiento (Tokens)
    kw_vto_target = kw_vto.strip() if kw_vto else "VENCIMIENTO"
    fecha_vto_val = buscar_por_tokens(texto_unido, kw_vto_target, es_fecha=True)
    if fecha_vto_val:
        fecha_vencimiento = fecha_vto_val

    # Fallbacks de respaldo
    if not fecha_cierre:
        fecha_cierre = fecha_mail_fmt
    if not fecha_vencimiento:
        fecha_vencimiento = fecha_cierre

    # 3. Extracción del Monto Total (Tokens)
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
                # --- Identificación Dinámica de Columnas (BNA vs Naranja) ---
                grupos = m.groups()
                if len(grupos) == 6:
                    # Formato Naranja con Tarjeta: fecha, tarjeta, comprobante, detalle, cuota, pesos
                    fecha, tarjeta, comprobante, detalle, cuota_detectada, pesos = grupos
                    detalle_limpio, cuota_actual, cuota_total = extraer_cuotas(detalle, cuota_detectada)
                    
                    pesos_val = normalizar_monto(pesos)
                    # Regla Matemática Plan Zeta: Se divide por 3
                    if cuota_detectada.strip().upper() == "ZETA":
                        pesos_val = round(pesos_val / 3.0, 2)
                        
                    dolar_val = 0.0
                    detalle_final = f"{detalle_limpio} ({tarjeta.strip()})"
                elif len(grupos) == 5:
                    g4 = str(grupos[3]).strip()
                    if g4.upper() == "ZETA" or "/" in g4 or (g4.isdigit() and len(g4) == 2 and int(g4) <= 36):
                        # Formato Naranja sin Tarjeta: fecha, comprobante, detalle, cuota, pesos
                        fecha, comprobante, detalle, cuota_detectada, pesos = grupos
                        detalle_limpio, cuota_actual, cuota_total = extraer_cuotas(detalle, cuota_detectada)
                        
                        pesos_val = normalizar_monto(pesos)
                        # Regla Matemática Plan Zeta: Se divide por 3
                        if cuota_detectada.strip().upper() == "ZETA":
                            pesos_val = round(pesos_val / 3.0, 2)
                            
                        dolar_val = 0.0
                        detalle_final = detalle_limpio
                    else:
                        # Formato BNA Visa: fecha, comprobante, detalle, pesos, dolar
                        fecha, comprobante, detalle, pesos, dolar = grupos
                        detalle_limpio, cuota_actual, cuota_total = extraer_cuotas(detalle)
                        pesos_val = normalizar_monto(pesos)
                        dolar_val = normalizar_monto(dolar)
                        detalle_final = detalle_limpio
                elif len(grupos) == 4:
                    # Formato alternativo sin dólares (como Naranja): fecha, comprobante, detalle, cuota, pesos
                    fecha, comprobante, detalle, cuota_detectada, pesos = grupos
                    detalle_limpio, cuota_actual, cuota_total = extraer_cuotas(detalle, cuota_detectada)
                    
                    pesos_val = normalizar_monto(pesos)
                    # Regla Matemática Plan Zeta: Se divide por 3
                    if cuota_detectada.strip().upper() == "ZETA":
                        pesos_val = round(pesos_val / 3.0, 2)
                        
                    dolar_val = 0.0
                    detalle_final = detalle_limpio
                else:
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


# ---------- Procesamiento de Telegram ( getUpdates ) ----------

def leer_config_completo(ws_config):
    """Lee toda la hoja Config de forma segura y devuelve la memoria conversacional."""
    valores = ws_config.get_all_values()
    last_update_id = 0
    state = ""
    tipos = []
    
    if len(valores) > 1:
        # Columna B: Last_Telegram_Update_ID
        if len(valores[1]) > 1 and valores[1][1]:
            try:
                last_update_id = int(valores[1][1])
            except Exception:
                pass
        # Columna C: Telegram_State
        if len(valores[1]) > 2:
            state = str(valores[1][2]).strip()
            
    # Columna E: Tipo (Categorías de gastos manuales), desde la fila 2 hacia abajo
    for fila in valores[1:]:
        if len(fila) > 4 and fila[4].strip():
            tipos.append(fila[4].strip())
            
    return last_update_id, state, tipos


def guardar_estado_telegram(ws_config, estado):
    try:
        # Guarda el estado de la conversación en la celda C2 (Telegram_State)
        ws_config.update_cell(2, 3, estado)
    except Exception as e:
        print(f"[DEBUG] Error al guardar estado en Config: {str(e)}")


def parsear_monto_manual(texto):
    """Detecta números o expresiones como 100mil o $5000 en el mensaje de texto."""
    texto_limpio = str(texto).strip().lower().replace("$", "").replace(" ", "")
    
    # Soporte para abreviatura "mil" (ej: 100mil -> 100000)
    factor = 1.0
    if "mil" in texto_limpio:
        factor = 1000.0
        texto_limpio = texto_limpio.replace("mil", "")
        
    m = re.match(r'^(\d+(?:[.,]\d{1,2})?)$', texto_limpio)
    if m:
        try:
            num = normalizar_monto(texto_limpio)
            return round(num * factor, 2)
        except Exception:
            pass
    return None


def enviar_teclado_categorias(chat_id, monto, tipos):
    """Envía un teclado interactivo por Telegram con tus categorías configuradas."""
    keyboard = []
    fila = []
    for t in tipos:
        fila.append({"text": t, "callback_data": t})
        if len(fila) == 2:
            keyboard.append(fila)
            fila = []
    if fila:
        keyboard.append(fila)
        
    reply_markup = {"inline_keyboard": keyboard}
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": f"❓ ¿A qué categoría corresponde el gasto manual de ${monto:,.2f}?",
        "reply_markup": reply_markup
    }
    requests.post(url, json=payload)


def procesar_mensajes_telegram(reglas, ws_consolidado, ws_consumos, ws_config):
    """Llamada a getUpdates para procesar archivos PDF o gastos manuales por botones."""
    print("[DEBUG] Buscando mensajes nuevos en Telegram...")
    last_update_id, state, tipos = leer_config_completo(ws_config)
    offset = last_update_id + 1 if last_update_id > 0 else 0
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={offset}"
    try:
        updates = requests.get(url, timeout=15).json().get("result", [])
    except Exception as e:
        print(f"[ERROR] No se pudo obtener actualizaciones de Telegram: {str(e)}")
        return

    if not updates:
        print("[DEBUG] No hay mensajes nuevos en Telegram.")
        return

    print(f"[DEBUG] Se encontraron {len(updates)} actualizaciones en Telegram.")
    for update in updates:
        update_id = update["update_id"]
        
        # CASO 1: Callback Query (Clic en botón de categoría)
        callback_query = update.get("callback_query")
        if callback_query:
            chat_id = str(callback_query["message"]["chat"]["id"])
            if chat_id == TELEGRAM_CHAT_ID:
                data_seleccionada = callback_query["data"]
                message_id = callback_query["message"]["message_id"]
                
                # Re-leer estado para obtener el monto guardado
                _, state_actual, _ = leer_config_completo(ws_config)
                if state_actual.startswith("ESPERANDO_TIPO|"):
                    try:
                        monto_str = state_actual.split("|")[1]
                        monto_val = float(monto_str)
                        fecha_hoy = datetime.now(ZoneInfo("America/Argentina/Cordoba")).strftime("%d/%m/%Y")
                        
                        # Guardar el gasto manual directamente en la hoja Consumos
                        if ws_consumos is not None:
                            ws_consumos.append_row([
                                fecha_hoy,
                                "Telegram",
                                data_seleccionada,
                                1,
                                1,
                                monto_val,
                                0.0,
                                "",
                                "",
                                "Manual Telegram"
                            ], value_input_option="USER_ENTERED")
                            
                        # Editar el mensaje de Telegram para quitar los botones y confirmar
                        url_edit = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/editMessageText"
                        payload_edit = {
                            "chat_id": chat_id,
                            "message_id": message_id,
                            "text": f"✅ ¡Gasto de ${monto_val:,.2f} registrado con éxito en '{data_seleccionada}'!"
                        }
                        requests.post(url_edit, json=payload_edit)
                        guardar_estado_telegram(ws_config, "")
                        
                    except Exception as e:
                        print(f"[ERROR] Error al procesar selección de botón: {str(e)}")
                        enviar_telegram(f"❌ Error al registrar gasto manual: {str(e)}")
                        
            # Marcar mensaje como procesado
            ws_config.update_cell(2, 2, update_id)
            continue

        # CASO 2: Mensaje común de texto o archivo
        message = update.get("message")
        if not message:
            continue
            
        chat_id = str(message["chat"]["id"])
        if chat_id != TELEGRAM_CHAT_ID:
            continue

        # A. Procesamiento de Texto (Gastos Manuales)
        texto = message.get("text")
        if texto:
            monto_detectado = parsear_monto_manual(texto)
            if monto_detectado is not None:
                print(f"[DEBUG] Gasto manual detectado por texto: {monto_detectado}")
                guardar_estado_telegram(ws_config, f"ESPERANDO_TIPO|{monto_detectado}")
                enviar_teclado_categorias(chat_id, monto_detectado, tipos)
                ws_config.update_cell(2, 2, update_id)
                continue

        # B. Procesamiento de Documento (Resúmenes en PDF)
        document = message.get("document")
        if document and document.get("mime_type") == "application/pdf":
            file_id = document["file_id"]
            file_name = document.get("file_name", "Factura.pdf")
            print(f"[DEBUG] PDF recibido por Telegram: '{file_name}'")
            
            try:
                get_file_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile?file_id={file_id}"
                file_path = requests.get(get_file_url).json()["result"]["file_path"]
                download_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
                pdf_bytes = requests.get(download_url).content
                print("[DEBUG] PDF descargado temporalmente en memoria.")
                
                texto_completo_pdf = ""
                with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                    for pagina in pdf.pages:
                        texto_completo_pdf += (pagina.extract_text() or "") + "\n"
                
                regla = identificar_regla_por_pdf(texto_completo_pdf, reglas)
                if not regla:
                    print("[ERROR] No se pudo identificar la empresa/regla de este PDF.")
                    enviar_telegram("❌ Error: No logré identificar a qué empresa pertenece esta factura. Verifica que la regla esté activa en la hoja Datos.")
                    ws_config.update_cell(2, 2, update_id)
                    continue
                
                remitente = regla["Remitente"]
                clave = regla.get("Clave", "")
                tiene_adjunto = regla.get("Tiene_Adjunto", "NO") == "SI"
                es_tarjeta = regla.get("Es_Tarjeta_Credito", "NO") == "SI"
                print(f"[DEBUG] PDF identificado como de: {remitente}")
                
                pdf_sin_clave = quitar_clave_pdf(pdf_bytes, clave) if clave else pdf_bytes
                
                link_drive = ""
                if "epec.com.ar" not in remitente.lower():
                    link_drive = subir_a_drive(file_name, pdf_sin_clave)
                    
                fecha_mail_fmt = datetime.now(ZoneInfo("America/Argentina/Cordoba")).strftime("%d/%m/%Y")
                consumos, fecha_cierre, fecha_vencimiento, monto_total = extraer_consumos_pdf(
                    pdf_sin_clave, "", fecha_mail_fmt, regla
                )
                
                # --- Guardar Deduplicado ---
                if es_registro_duplicado(ws_consolidado, remitente, monto_total, fecha_vencimiento):
                    print(f"[DEBUG] Registro duplicado detectado desde Telegram para {remitente} (${monto_total}). Se omite.")
                    enviar_telegram(f"⚠️ El archivo enviado de {remitente} ya fue procesado anteriormente (Monto: ${monto_total:,.2f}, Vto: {fecha_vencimiento}).")
                    ws_config.update_cell(2, 2, update_id)
                    continue

                if ws_consumos is not None and es_tarjeta and consumos:
                    guardar_consumos_sheet(ws_consumos, consumos, remitente)
                    
                guardar_en_sheet(ws_consolidado, datetime.now().strftime("%a, %d %b %Y %H:%M:%S -0000"), f"Resumen recibido por Telegram ({file_name})", monto_total, fecha_vencimiento, remitente, link_drive)
                
                confirmacion = f"✅ ¡Factura procesada con éxito desde Telegram!\n🏢 Empresa: {remitente}\n💵 Monto: ${monto_total:,.2f}\n📅 Vencimiento: {fecha_vencimiento}"
                if link_drive:
                    confirmacion += f"\n📂 Archivo: {link_drive}"
                enviar_telegram(confirmacion)
                
            except Exception as e:
                print(f"[ERROR] Error al procesar PDF de Telegram: {str(e)}")
                enviar_telegram(f"❌ Ocurrió un error al procesar tu archivo '{file_name}': {str(e)}")
                
        # Confirmar procesamiento
        ws_config.update_cell(2, 2, update_id)


# ---------- Flujo de Trabajo Principal ----------

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
    ws_config = sh.worksheet("Config")
    try:
        ws_consumos = sh.worksheet("Consumos")
    except gspread.WorksheetNotFound:
        print("[DEBUG] Hoja 'Consumos' no existe, se omitirá el guardado de consumos.")
        ws_consumos = None
    print("[DEBUG] Hojas abiertas correctamente.")

    # 1. Procesar Gmail
    print(f"[DEBUG] Procesando {len(reglas)} reglas de Gmail...\n")
    for idx, regla in enumerate(reglas, 1):
        print(f"\n--- Regla {idx} ---")
        remitente = regla["Remitente"]
        asunto_contiene = regla.get("Asunto_Contiene", "")
        clave = str(regla.get("Clave", "")).strip()
        tiene_adjunto = str(regla.get("Tiene_Adjunto", "NO")).strip().upper() == "SI"
        es_tarjeta = str(regla.get("Es_Tarjeta_Credito", "NO")).strip().upper() == "SI"
        
        print(f"[DEBUG] Remitente: {remitente}")
        print(f"[DEBUG] Asunto contiene: '{asunto_contiene}'")
        print(f"[DEBUG] Tiene Adjunto: {'SI' if tiene_adjunto else 'NO'}")
        print(f"[DEBUG] Es Tarjeta de Crédito: {'SI' if es_tarjeta else 'NO'}")

        nuevos = buscar_mails_nuevos(remitente, asunto_contiene)

        for m in nuevos:
            asunto, fecha, cuerpo_texto, cuerpo_raw, pdf_filename, pdf_bytes = extraer_datos_mensaje_mime(m["id"])
            link_drive = ""
            fecha_mail_fmt = formatear_fecha_resumen(fecha)
            monto_total = 0.0
            fecha_vencimiento = ""

            # CASO A: Regla configurada con Tiene_Adjunto = SI
            if tiene_adjunto:
                if not pdf_bytes:
                    pdf_filename, pdf_bytes = descargar_pdf_desde_link(cuerpo_raw)

                if pdf_bytes:
                    try:
                        pdf_sin_clave = quitar_clave_pdf(pdf_bytes, clave) if clave else pdf_bytes
                        
                        if "epec.com.ar" not in remitente.lower():
                            link_drive = subir_a_drive(pdf_filename or "Factura.pdf", pdf_sin_clave)
                            print(f"[DEBUG] Archivo subido exitosamente a Google Drive: {link_drive}")
                        else:
                            print("[DEBUG] Remitente es EPEC, se procesa temporalmente en memoria sin subir a Google Drive.")
                            link_drive = ""

                        consumos, _, fecha_vencimiento, monto_total = extraer_consumos_pdf(
                            pdf_sin_clave, cuerpo_texto, fecha_mail_fmt, regla
                        )
                        
                        if ws_consumos is not None and es_tarjeta and consumos:
                            guardar_consumos_sheet(ws_consumos, consumos, remitente)
                    except pikepdf.PasswordError:
                        print("[ERROR] Clave de PDF incorrecta")

            # CASO B: Si Tiene_Adjunto es NO o si la lectura del PDF falló / no encontró datos
            if not fecha_vencimiento or monto_total == 0.0:
                if not tiene_adjunto:
                    kw_cierre = str(regla.get("Regex_Cierre", "")).strip()
                    kw_vto = str(regla.get("Regex_Vencimiento", "")).strip()
                    kw_monto = str(regla.get("Regex_Monto", "")).strip()
                    _, fecha_vencimiento, monto_total = extraer_fechas_y_monto_global("", cuerpo_texto, fecha_mail_fmt, kw_cierre, kw_vto, kw_monto)

            # --- Guardar Deduplicado ---
            if es_registro_duplicado(ws_consolidado, remitente, monto_total, fecha_vencimiento):
                print(f"[DEBUG] Registro duplicado detectado para {remitente} (${monto_total}). Se omite.")
                marcar_procesado(m["id"], label_id)
                continue

            guardar_en_sheet(ws_consolidado, fecha, asunto, monto_total, fecha_vencimiento, remitente, link_drive)
            
            texto_telegram = f"📩 Resumen Procesado\nDe: {remitente}\nAsunto: {asunto}\nMonto: ${monto_total:,.2f}\nVencimiento: {fecha_vencimiento}"
            if link_drive:
                texto_telegram += f"\nPDF: {link_drive}"
            enviar_telegram(texto_telegram)
            
            marcar_procesado(m["id"], label_id)
            total_procesados += 1

    # 2. Procesar Telegram (Archivos o Gastos Manuales)
    print("\n" + "="*60)
    procesar_mensajes_telegram(reglas, ws_consolidado, ws_consumos, ws_config)
    print("="*60 + "\n")

    print(f"[RESULTADO] {total_procesados} mail(s) procesado(s).")


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
