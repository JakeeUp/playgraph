import logging

from app.security_logging import OmitQueryString


def test_access_logs_omit_callback_assertions():
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                               ("localhost", "GET", "/auth/steam/callback?state=private", "1.1", 200), None)
    assert OmitQueryString().filter(record)
    assert "private" not in record.getMessage()
    assert "/auth/steam/callback" in record.getMessage()
