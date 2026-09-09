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


def test_json2_inspection_and_module_methods(monkeypatch):
    calls = []

    class Response:
        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self.data

    class Client:
        def __init__(self, **kwargs):
            pass

        def post(self, path, json):
            calls.append((path, json))
            if "ir.ui.view" in path:
                return Response([{"name": "test_view", "type": "form"}])
            if "ir.model/search_read" in path:
                return Response([{"id": 42}])
            if "ir.model.access" in path:
                return Response([{"name": "access_rule_1", "perm_read": True}])
            if "ir.rule" in path:
                return Response([{"name": "record_rule_1"}])
            if "ir.module.module/search_read" in path:
                return Response([{"id": 99, "state": "uninstalled"}])
            if "button_immediate_install" in path:
                return Response(True)
            if "button_immediate_upgrade" in path:
                return Response(True)
            return Response([{"id": 1}])

    monkeypatch.setattr("agent.httpx.Client", Client)
    client = OdooJSON2Client("https://odoo.example.com", "staging", "test-key")

    # get_views
    views = client.get_views("sale.order")
    assert len(views) == 1
    assert views[0]["name"] == "test_view"

    # get_access_rules
    access = client.get_access_rules("sale.order")
    assert len(access) == 1
    assert access[0]["name"] == "access_rule_1"

    # get_record_rules
    rules = client.get_record_rules("sale.order")
    assert len(rules) == 1
    assert rules[0]["name"] == "record_rule_1"

    # install_module
    res = client.install_module("sale")
    assert res is True

    # upgrade_module
    res = client.upgrade_module("sale")
    assert res is True


def test_odoo_client_inspection_and_module_methods(monkeypatch):
    from unittest.mock import MagicMock
    from agent import OdooClient

    mock_common = MagicMock()
    mock_common.authenticate.return_value = 1
    mock_models = MagicMock()

    monkeypatch.setattr("xmlrpc.client.ServerProxy", lambda url, **kwargs: mock_common if "common" in url else mock_models)

    client = OdooClient("https://odoo.example.com", "staging", "admin", "admin")

    mock_models.execute_kw.return_value = [{"name": "test_view", "type": "form"}]
    views = client.get_views("res.partner")
    assert len(views) == 1

    mock_models.execute_kw.return_value = [{"id": 10}]
    client.search_read = MagicMock(return_value=[{"id": 10}])
    access = client.get_access_rules("res.partner")
    assert access == [{"id": 10}]

    mock_models.execute_kw.return_value = True
    assert client.install_module("crm") is True
    assert client.upgrade_module("crm") is True


def test_pi_erp_client_subclass():
    from agent import PiERPClient
    from pri_erp_adapter import PriERPAdapter

    client = PiERPClient("https://admin.sh.prierp.com", api_key="secret-key")
    assert isinstance(client, PriERPAdapter)
    assert client.erp_type == "pri_erp"

