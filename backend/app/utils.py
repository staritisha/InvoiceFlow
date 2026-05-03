import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from app.config import settings


def send_email(to_email: str, subject: str, body: str):
    if not settings.email_host:
        raise Exception("Email not configured")

    msg = MIMEMultipart()
    msg["From"] = settings.email_from
    msg["To"] = to_email
    msg["Subject"] = subject

    msg.attach(MIMEText(body, "plain"))

    try:
        server = smtplib.SMTP(settings.email_host, settings.email_port)
        server.starttls()
        server.login(settings.email_username, settings.email_password)
        server.send_message(msg)
        server.quit()
    except Exception as e:
        raise Exception(f"Email failed: {str(e)}")