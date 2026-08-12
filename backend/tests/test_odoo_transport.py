from agent import OdooJSON2Client, TimeoutSafeTransport, TimeoutTransport, xmlrpc_transport


def test_http_odoo_uses_plain_transport():
    transport = xmlrpc_transport("http://localhost:8069")
    assert isinstance(transport, TimeoutTransport)
    assert not isinstance(transport, TimeoutSafeTransport)


def test_https_odoo_uses_tls_transport():
    assert isinstance(xmlrpc_transport("https://odoo.example.com"), TimeoutSafeTransport)


def test_json2_uses_scoped_headers_timeout_and_no_redirects(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return [{"id": 1}]

    class Client:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def post(self, path, json):
            captured["path"] = path; captured["payload"] = json
            return Response()

    monkeypatch.setattr("agent.httpx.Client", Client)
    client = OdooJSON2Client("https://odoo.example.com", "staging", "scoped-key")
    assert captured["headers"]["X-Odoo-Database"] == "staging"
    assert captured["headers"]["Authorization"] == "bearer scoped-key"
    assert captured["follow_redirects"] is False
    assert captured["timeout"] == 10
    assert captured["path"] == "/json/2/res.users/search_read"
    assert client.search_read("res.company", [], ["name"], 1) == [{"id": 1}]
