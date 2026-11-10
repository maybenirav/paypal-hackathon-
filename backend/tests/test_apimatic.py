"""APIMatic validation script, with APIMatic's response faked (no network in tests)."""
import importlib.util
from pathlib import Path

import httpx

_spec = importlib.util.spec_from_file_location(
    "apimatic_validate", Path(__file__).resolve().parent.parent / "scripts" / "apimatic_validate.py")
av = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(av)


def test_validate_sends_spec_and_reports():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["Authorization"]
        seen["url"] = str(request.url)
        seen["multipart"] = b'filename="planlab-openapi.json"' in request.read()
        return httpx.Response(200, json={"success": True, "errors": [], "messages": [], "warnings": [
            'Name too long.  (<a href="https://docs.apimatic.io/x">View Details</a>)'] * 2})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    spec = av.app.openapi()
    result = av.validate(spec, "k123", client)
    assert seen == {"auth": "X-Auth-Key k123", "url": av.URL, "multipart": True}
    md = av.report(result, spec)
    assert "**Valid:** yes" in md and "**Errors:** 0" in md
    assert "- 2 x Name too long. ([rule](https://docs.apimatic.io/x))" in md


def test_validate_raises_on_auth_error():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401, text="nope")))
    try:
        av.validate({}, "bad", client)
    except RuntimeError as e:
        assert "401" in str(e)
    else:
        raise AssertionError("expected RuntimeError")
