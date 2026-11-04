import os
import smtplib
import tempfile
from unittest.mock import patch

import pytest

from email_sender import SMTP_TIMEOUT_SECONDS, EmailSender


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


@pytest.fixture
def photo(tmp_path):
    path = tmp_path / "p.jpg"
    path.write_bytes(b"fake")
    return str(path)


@pytest.fixture(autouse=True)
def no_retry_delay():
    with patch("tenacity.nap.time.sleep"):
        yield


class TestSendOutcome:
    @patch("email_sender.smtplib.SMTP")
    def test_success(self, smtp, photo):
        assert _make_sender().send_photos("r@test.com", [photo]) is True
        smtp.return_value.sendmail.assert_called_once()

    @patch("email_sender.smtplib.SMTP")
    def test_failed_quit_after_send_still_counts_as_sent(self, smtp, photo):
        """A failure here would re-send the batch on the next run."""
        smtp.return_value.quit.side_effect = smtplib.SMTPServerDisconnected("dropped")

        assert _make_sender().send_photos("r@test.com", [photo]) is True
        assert smtp.return_value.sendmail.call_count == 1
        smtp.return_value.close.assert_called_once()

    @patch("email_sender.smtplib.SMTP")
    def test_connection_has_a_timeout(self, smtp, photo):
        _make_sender().send_photos("r@test.com", [photo])
        assert smtp.call_args.kwargs["timeout"] == SMTP_TIMEOUT_SECONDS


class TestRetries:
    @patch("email_sender.smtplib.SMTP")
    def test_temporary_rejection_retries_on_a_new_connection(self, smtp, photo):
        smtp.return_value.sendmail.side_effect = [
            smtplib.SMTPDataError(421, b"try later"),
            {},
        ]

        assert _make_sender().send_photos("r@test.com", [photo]) is True
        assert smtp.call_count == 2

    @patch("email_sender.smtplib.SMTP")
    def test_connection_errors_retry_then_fail(self, smtp, photo):
        smtp.side_effect = TimeoutError("timed out")

        assert _make_sender().send_photos("r@test.com", [photo]) is False
        assert smtp.call_count == 3

    @patch("email_sender.smtplib.SMTP")
    def test_bad_credentials_are_not_retried(self, smtp, photo):
        smtp.return_value.login.side_effect = smtplib.SMTPAuthenticationError(535, b"bad password")

        assert _make_sender().send_photos("r@test.com", [photo]) is False
        assert smtp.call_count == 1

    @patch("email_sender.smtplib.SMTP")
    def test_message_too_large_is_not_retried(self, smtp, photo):
        smtp.return_value.sendmail.side_effect = smtplib.SMTPDataError(552, b"too large")

        assert _make_sender().send_photos("r@test.com", [photo]) is False
        assert smtp.call_count == 1
