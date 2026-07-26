import os
import re
import io
import base64
import email.utils
from datetime import datetime

import requests
import gspread
import pikepdf
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

LABEL_PROCESADO = "Procesado-BNA"


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
    sh = gc.open_by_key(SHEET_ID)
    ws_datos = sh.worksheet("Datos")
    filas = ws_datos.get_all_records()
    return [f for f in filas if str(f.get("Activo", "")).strip().upper() == "SI"]


def buscar_mails_nuevos(remitente, asunto_contiene):
    query = f"from:{remitente} -label:{LABEL_PROCESADO}"
    if asunto_contiene:
        query += f' subject:"{asunto_contiene}"'
    resultados = gmail_service.users().messages().list(userId="me", q=query).execute()
    return resultados.get("messages", [])


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
    label_id = obtener_o_crear_label(LABEL_PROCESADO)
    reglas = obtener_reglas()
    total_procesados = 0

    for regla in reglas:
        remitente = regla["Remitente"]
        asunto_contiene = regla.get("Asunto_Contiene", "")
        clave = str(regla.get("Clave", "")).strip()
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
                    except pikepdf.PasswordError:
                        resumen = "ERROR: la clave del Sheet no coincide con la del PDF"

            guardar_en_sheet(fecha, asunto, resumen, remitente, link_drive)
            texto_telegram = f"📩 Recibiste tu resumen\nDe: {remitente}\nAsunto: {asunto}"
            if link_drive:
                texto_telegram += f"\nPDF: {link_drive}"
            enviar_telegram(texto_telegram)
            marcar_procesado(m["id"], label_id)
            total_procesados += 1

    print(f"{total_procesados} mail(s) procesado(s)." if total_procesados else "Sin mails nuevos.")


if __name__ == "__main__":
    revisar_mails()
