import logging
import mimetypes
import os
import smtplib
from datetime import datetime
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


class EmailSender:
    def __init__(self, smtp_server: str, smtp_port: int, sender_email: str, sender_password: str) -> None:
        self.smtp_server = smtp_server
        self.smtp_port = smtp_port
        self.sender_email = sender_email
        self.sender_password = sender_password

    def _build_message(
        self, recipient_email: str, photo_paths: list[str], subject: str | None = None
    ) -> MIMEMultipart:
        if subject is None:
            subject = f"New photos from iCloud album - {datetime.now().strftime('%Y-%m-%d %H:%M')}"

        msg = MIMEMultipart()
        msg["From"] = self.sender_email
        msg["To"] = recipient_email
        msg["Subject"] = subject

        body = (
            f"{len(photo_paths)} new photo(s) have been added to your iCloud shared album.\n"
            "Please find them attached to this email.\n\n"
            "Sent automatically by SkylightSync"
        )
        msg.attach(MIMEText(body, "plain"))

        for photo_path in photo_paths:
            if not os.path.exists(photo_path):
                logger.warning("Photo not found: %s", photo_path)
                continue

            mime_type, _ = mimetypes.guess_type(photo_path)
            if mime_type and mime_type.startswith("image/"):
                maintype, subtype = mime_type.split("/", 1)
            else:
                maintype, subtype = "image", "jpeg"

            with open(photo_path, "rb") as f:
                part = MIMEBase(maintype, subtype)
                part.set_payload(f.read())
                encoders.encode_base64(part)
                part.add_header(
                    "Content-Disposition",
                    f"attachment; filename={os.path.basename(photo_path)}",
                )
                msg.attach(part)

        return msg

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=True)
    def _smtp_send(self, server: smtplib.SMTP, recipient: str, msg: MIMEMultipart) -> None:
        server.sendmail(self.sender_email, recipient, msg.as_string())

    def send_photos(
        self,
        recipient_email: str,
        photo_paths: list[str],
        subject: str | None = None,
        server: smtplib.SMTP | None = None,
    ) -> bool:
        if not photo_paths:
            logger.info("No photos to send")
            return False

        msg = self._build_message(recipient_email, photo_paths, subject)
        own_connection = server is None

        try:
            if own_connection:
                server = smtplib.SMTP(self.smtp_server, self.smtp_port)
                server.starttls()
                server.login(self.sender_email, self.sender_password)

            self._smtp_send(server, recipient_email, msg)
            logger.info("Email sent successfully to %s", recipient_email)
            return True

        except Exception as e:
            logger.error("Error sending email: %s", e)
            return False

        finally:
            if own_connection and server is not None:
                server.quit()

    def send_photos_in_batches(
        self, recipient_email: str, photo_paths: list[str], batch_size: int = 5
    ) -> bool:
        """Send photos in batches over a single SMTP connection."""
        batches = [photo_paths[i : i + batch_size] for i in range(0, len(photo_paths), batch_size)]
        server: smtplib.SMTP | None = None

        try:
            server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            server.starttls()
            server.login(self.sender_email, self.sender_password)

            for i, batch in enumerate(batches):
                subject = f"Photos batch {i + 1}/{len(batches)} - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                success = self.send_photos(recipient_email, batch, subject, server=server)
                if not success:
                    logger.error("Failed to send batch %d", i + 1)
                    return False

            return True

        except Exception as e:
            logger.error("SMTP connection error: %s", e)
            return False

        finally:
            if server is not None:
                try:
                    server.quit()
                except smtplib.SMTPServerDisconnected:
                    pass
