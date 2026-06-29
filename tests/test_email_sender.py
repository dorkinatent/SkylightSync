import os
import tempfile
from unittest.mock import MagicMock, patch

from email_sender import EmailSender


def _make_sender() -> EmailSender:
    return EmailSender("smtp.test.com", 587, "sender@test.com", "password")


class TestBuildMessage:
    def test_basic_message(self):
        sender = _make_sender()
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"\xff\xd8\xff\xe0fake jpeg")
            path = f.name

        try:
            msg = sender._build_message("recipient@test.com", [path])
            assert msg["To"] == "recipient@test.com"
            assert msg["From"] == "sender@test.com"
            assert "iCloud album" in msg["Subject"]
            attachments = [p for p in msg.get_payload() if p.get_content_maintype() == "image"]
            assert len(attachments) == 1
        finally:
            os.unlink(path)

    def test_custom_subject(self):
        sender = _make_sender()
        msg = sender._build_message("r@test.com", [], subject="Custom Subject")
        assert msg["Subject"] == "Custom Subject"

    def test_missing_file_skipped(self):
        sender = _make_sender()
        msg = sender._build_message("r@test.com", ["/nonexistent/photo.jpg"])
        attachments = [p for p in msg.get_payload() if p.get_content_maintype() == "image"]
        assert len(attachments) == 0

    def test_jpeg_mime_type(self):
        sender = _make_sender()
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"fake jpeg")
            path = f.name

        try:
            msg = sender._build_message("r@test.com", [path])
            attachment = [p for p in msg.get_payload() if p.get_content_maintype() == "image"][0]
            assert attachment.get_content_type() == "image/jpeg"
        finally:
            os.unlink(path)

    def test_png_mime_type(self):
        sender = _make_sender()
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"fake png")
            path = f.name

        try:
            msg = sender._build_message("r@test.com", [path])
            attachment = [p for p in msg.get_payload() if p.get_content_maintype() == "image"][0]
            assert attachment.get_content_type() == "image/png"
        finally:
            os.unlink(path)


class TestBatchSplitting:
    @patch("email_sender.smtplib.SMTP")
    def test_batches_correct_count(self, mock_smtp_class):
        mock_server = MagicMock()
        mock_smtp_class.return_value = mock_server

        sender = _make_sender()
        paths = []
        for i in range(7):
            f = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
            f.write(b"fake")
            f.close()
            paths.append(f.name)

        try:
            sender.send_photos_in_batches("r@test.com", paths, batch_size=3)
            # 7 photos / batch_size 3 = 3 batches (3, 3, 1)
            assert mock_server.sendmail.call_count == 3
        finally:
            for p in paths:
                os.unlink(p)

    @patch("email_sender.smtplib.SMTP")
    def test_single_smtp_connection(self, mock_smtp_class):
        mock_server = MagicMock()
        mock_smtp_class.return_value = mock_server

        sender = _make_sender()
        paths = []
        for i in range(4):
            f = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
            f.write(b"fake")
            f.close()
            paths.append(f.name)

        try:
            sender.send_photos_in_batches("r@test.com", paths, batch_size=2)
            # Only one SMTP connection should be created
            mock_smtp_class.assert_called_once()
            mock_server.login.assert_called_once()
        finally:
            for p in paths:
                os.unlink(p)
