import os
import re
import io
import base64
import email.utils
from datetime import datetime

import requests
import gspread
import pikepdf
import pdfplumber
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# ---------- Configuración desde variables de entorno (secrets) ----------
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
SHEET_ID = os.environ["SHEET_ID"]
SHEET_NAME = "Consolidado"

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

gmail_service = build("gmail", "v1", credentials=creds)
drive_service = build("drive", "v3", credentials=creds)
gc = gspread.authorize(creds)

LABEL_PROCESADO = "Procesado-Resumen"
from zoneinfo import ZoneInfo

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
        return True  # si no hay configuración, ejecuta siempre

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

# ---------- Funciones ----------
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


def obtener_reglas():
    print("[DEBUG] Leyendo reglas de la hoja Datos...")
    sh = gc.open_by_key(SHEET_ID)
    ws_datos = sh.worksheet("Datos")
    filas = ws_datos.get_all_records()
    reglas_activas = [f for f in filas if str(f.get("Activo", "")).strip().upper() == "SI"]
    print(f"[DEBUG] Reglas activas encontradas: {len(reglas_activas)}")
    for i, r in enumerate(reglas_activas, 1):
        print(f"  Regla {i}: Remitente={r.get('Remitente')}, Asunto={r.get('Asunto_Contiene', '')}")
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


def extraer_texto(mensaje_id):
    msg = gmail_service.users().messages().get(userId="me", id=mensaje_id, format="full").execute()
    headers = msg["payload"]["headers"]
    asunto = next((h["value"] for h in headers if h["name"] == "Subject"), "(sin asunto)")
    fecha = next((h["value"] for h in headers if h["name"] == "Date"), "")

    def obtener_body(payload, mime_deseado):
        if payload.get("mimeType") == mime_deseado:
            data = payload["body"].get("data")
            if data:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
        if "parts" in payload:
            for parte in payload["parts"]:
                texto = obtener_body(parte, mime_deseado)
                if texto:
                    return texto
        return ""

    cuerpo = obtener_body(msg["payload"], "text/plain")
    if not cuerpo:
        cuerpo = limpiar_html(obtener_body(msg["payload"], "text/html"))

    return asunto, fecha, cuerpo[:500]


def buscar_adjunto_pdf(mensaje_id):
    msg = gmail_service.users().messages().get(userId="me", id=mensaje_id, format="full").execute()

    def buscar_parte(payload):
        if "parts" in payload:
            for parte in payload["parts"]:
                resultado = buscar_parte(parte)
                if resultado:
                    return resultado
        if payload.get("filename", "").lower().endswith(".pdf"):
            attachment_id = payload["body"].get("attachmentId")
            if attachment_id:
                return payload["filename"], attachment_id
        return None

    resultado = buscar_parte(msg["payload"])
    if not resultado:
        return None, None

    filename, attachment_id = resultado
    adjunto = gmail_service.users().messages().attachments().get(
        userId="me", messageId=mensaje_id, id=attachment_id
    ).execute()
    return filename, base64.urlsafe_b64decode(adjunto["data"])


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


def formatear_fecha(fecha_rfc2822):
    try:
        dt = email.utils.parsedate_to_datetime(fecha_rfc2822)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return fecha_rfc2822


def guardar_en_sheet(fecha, asunto, resumen, remitente, link_drive=""):
    sh = gc.open_by_key(SHEET_ID)
    ws = sh.worksheet(SHEET_NAME)
    ws.append_row([formatear_fecha(fecha), remitente, asunto, resumen, link_drive])


def marcar_procesado(mensaje_id, label_id):
    gmail_service.users().messages().modify(
        userId="me", id=mensaje_id, body={"addLabelIds": [label_id]}
    ).execute()


def revisar_mails():
    print("\n" + "="*60)
    print("[INICIO] revisar_mails()")
    print("="*60)
    label_id = obtener_o_crear_label(LABEL_PROCESADO)
    print(f"[DEBUG] Label ID: {label_id}")
    reglas = obtener_reglas()
    total_procesados = 0

    print(f"[DEBUG] Procesando {len(reglas)} reglas...\n")
    for idx, regla in enumerate(reglas, 1):
        print(f"\n--- Regla {idx} ---")
        remitente = regla["Remitente"]
        asunto_contiene = regla.get("Asunto_Contiene", "")
        clave = str(regla.get("Clave", "")).strip()
        print(f"[DEBUG] Remitente: {remitente}")
        print(f"[DEBUG] Asunto contiene: '{asunto_contiene}'")
        print(f"[DEBUG] Clave PDF: {'[configurada]' if clave else '[sin clave]'}")
        nuevos = buscar_mails_nuevos(remitente, asunto_contiene)

        for m in nuevos:
            asunto, fecha, resumen = extraer_texto(m["id"])
            link_drive = ""

            if clave:
                nombre_archivo, pdf_bytes = buscar_adjunto_pdf(m["id"])
                if pdf_bytes:
                    try:
                        pdf_sin_clave = quitar_clave_pdf(pdf_bytes, clave)
                        link_drive = subir_a_drive(nombre_archivo, pdf_sin_clave)
                        regex_personalizado = regla.get("Regex_Consumo", "")
                        consumos = extraer_consumos_pdf(pdf_sin_clave, regex_personalizado)
                        guardar_consumos_sheet(consumos, remitente, link_drive)
                    except pikepdf.PasswordError:
                        resumen = "ERROR: la clave del Sheet no coincide con la del PDF"

            guardar_en_sheet(fecha, asunto, resumen, remitente, link_drive)
            texto_telegram = f"📩 Recibiste tu resumen\nDe: {remitente}\nAsunto: {asunto}"
            if link_drive:
                texto_telegram += f"\nPDF: {link_drive}"
            enviar_telegram(texto_telegram)
            marcar_procesado(m["id"], label_id)
            total_procesados += 1

    print(f"\n" + "="*60)
    print(f"[RESULTADO] {total_procesados} mail(s) procesado(s).")
    print("="*60 + "\n")


if __name__ == "__main__":
    REGEX_CONSUMO_DEFAULT = r'^(\d{2}\.\d{2}\.\d{2})\s+(?:(\d+)\s+)?(.+?)\s+(-?\d[\d.]*,\d{2})\s+(-?\d[\d.]*,\d{2})\s*$'


def normalizar_monto(texto):
    return float(texto.replace('.', '').replace(',', '.'))


def extraer_consumos_pdf(pdf_bytes, regex_personalizado=""):
    patron = re.compile(regex_personalizado.strip() or REGEX_CONSUMO_DEFAULT)
    consumos = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for pagina in pdf.pages:
            texto = pagina.extract_text() or ""
            for linea in texto.split("\n"):
                m = patron.match(linea.strip())
                if m:
                    fecha, comprobante, detalle, pesos, dolar = m.groups()
                    consumos.append({
                        "fecha": fecha,
                        "comprobante": comprobante or "",
                        "detalle": detalle.strip(),
                        "pesos": normalizar_monto(pesos),
                        "dolar": normalizar_monto(dolar),
                    })
    return consumos


def guardar_consumos_sheet(consumos, remitente, link_drive):
    if not consumos:
        return
    sh = gc.open_by_key(SHEET_ID)
    ws = sh.worksheet("Consumos")
    filas = [
        [c["fecha"], c["comprobante"], c["detalle"], c["pesos"], c["dolar"], remitente, link_drive]
        for c in consumos
    ]
    ws.append_rows(filas)
    if debe_ejecutar_ahora():
        revisar_mails()
    else:
        print("Todavía no es la hora configurada en el Sheet.")
