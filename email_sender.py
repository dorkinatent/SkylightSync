import logging
import mimetypes
import os
import smtplib
from datetime import datetime
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

# Bounds every socket operation so a stalled server can't hang a sync forever.
# Python applies this to a whole sendall(), so it must cover uploading a full
# ~25 MB message on a slow connection.
SMTP_TIMEOUT_SECONDS = 300


def _is_transient(exc: BaseException) -> bool:
    """Worth retrying: network failures and 4xx replies. Bad credentials and 5xx
    rejections (e.g. message too large) will fail the same way every time.

    A connection lost mid-send is ambiguous: the server may have accepted the
    message, so a retry can duplicate it. Delivery is at-least-once regardless,
    because an unconfirmed batch is re-sent on the next run anyway."""
    if isinstance(exc, smtplib.SMTPResponseException):
        return 400 <= exc.smtp_code < 500
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        return True
    if isinstance(exc, smtplib.SMTPException):
        return False
    return isinstance(exc, OSError)


def _close_quietly(server: smtplib.SMTP) -> None:
    """End the session without letting cleanup errors change the send's outcome:
    once the message is accepted, a failed QUIT must not read as a failed send,
    or the batch would be emailed again on the next run."""
    try:
        server.quit()
    except OSError as e:  # includes every smtplib.SMTPException
        logger.debug("Ignoring error while closing SMTP connection: %s", e)
        server.close()


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

    @retry(
        retry=retry_if_exception(_is_transient),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def _send_once(self, recipient: str, msg: MIMEMultipart) -> None:
        """One full connect/login/send attempt. Retries need a new connection,
        since a dropped SMTP session can't be reused."""
        server = smtplib.SMTP(self.smtp_server, self.smtp_port, timeout=SMTP_TIMEOUT_SECONDS)
        try:
            server.starttls()
            server.login(self.sender_email, self.sender_password)
            server.sendmail(self.sender_email, recipient, msg.as_string())
        finally:
            _close_quietly(server)

    def send_photos(
        self, recipient_email: str, photo_paths: list[str], subject: str | None = None
    ) -> bool:
        if not photo_paths:
            logger.info("No photos to send")
            return False

        msg = self._build_message(recipient_email, photo_paths, subject)

        try:
            self._send_once(recipient_email, msg)
        except Exception as e:
            logger.error("Error sending email: %s", e)
            return False

        logger.info("Email sent successfully to %s", recipient_email)
        return True
