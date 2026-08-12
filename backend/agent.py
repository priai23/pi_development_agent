import asyncio
import json
import socket
import xmlrpc.client
import httpx
from pathlib import Path
from typing import Any
from datetime import datetime, timezone
import hashlib

from database import SessionLocal
import models
import validation
import deployment

from langchain.tools import tool
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from workspace import Workspace


class _TimeoutMixin:
    def __init__(self, timeout: float = 8.0):
        super().__init__()
        self.timeout = timeout

    def make_connection(self, host):
        connection = super().make_connection(host)
        connection.timeout = self.timeout
        return connection


class TimeoutTransport(_TimeoutMixin, xmlrpc.client.Transport):
    pass


class TimeoutSafeTransport(_TimeoutMixin, xmlrpc.client.SafeTransport):
    pass


def xmlrpc_transport(url: str):
    return TimeoutSafeTransport() if url.lower().startswith("https://") else TimeoutTransport()


class OdooClient:
    def __init__(self, url: str, db: str, username: str, password: str):
        self.url = url.rstrip("/")
        self.db = db
        self.username = username
        self.password = password
        transport = xmlrpc_transport(self.url)
        self.common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common", transport=transport, allow_none=True)
        self.uid = self.common.authenticate(db, username, password, {})
        if not self.uid:
            raise ValueError("Authentication failed")
        self.models = xmlrpc.client.ServerProxy(
            f"{self.url}/xmlrpc/2/object", transport=xmlrpc_transport(self.url), allow_none=True
        )

    def version(self) -> dict:
        return self.common.version()

    def search_read(self, model: str, domain: list, fields: list[str], limit: int | None = None):
        kwargs: dict[str, Any] = {"fields": fields}
        if limit is not None:
            kwargs["limit"] = limit
        return self.models.execute_kw(
            self.db, self.uid, self.password, model, "search_read", [domain], kwargs
        )

    def update_company_contact(self, company_id: int, email: str, phone: str | None = None) -> bool:
        values = {"email": email}
        if phone is not None:
            values["phone"] = phone
        return self.models.execute_kw(
            self.db, self.uid, self.password, "res.company", "write", [[company_id], values]
        )

    def create(self, model: str, values: dict) -> int:
        return self.models.execute_kw(self.db, self.uid, self.password, model, "create", [values])

    def call(self, model: str, method: str, ids: list[int], **kwargs):
        return self.models.execute_kw(self.db, self.uid, self.password, model, method, [ids], kwargs)


class OdooJSON2Client:
    def __init__(self, url: str, db: str, api_key: str):
        self.url = url.rstrip("/")
        self.db = db
        self.api_key = api_key
        self.http = httpx.Client(
            base_url=self.url,
            headers={"Authorization": f"bearer {api_key}", "X-Odoo-Database": db},
            timeout=10,
            follow_redirects=False,
        )
        self.search_read("res.users", [], ["id"], 1)

    def call(self, model: str, method: str, **params):
        response = self.http.post(f"/json/2/{model}/{method}", json=params)
        response.raise_for_status()
        return response.json()

    def version(self) -> dict:
        response = self.http.get("/web/webclient/version_info")
        response.raise_for_status()
        return response.json()

    def search_read(self, model: str, domain: list, fields: list[str], limit: int | None = None):
        params: dict[str, Any] = {"domain": domain, "fields": fields}
        if limit is not None:
            params["limit"] = limit
        return self.call(model, "search_read", **params)

    def update_company_contact(self, company_id: int, email: str, phone: str | None = None) -> bool:
        values = {"email": email}
        if phone is not None:
            values["phone"] = phone
        return self.call("res.company", "write", ids=[company_id], vals=values)

    def create(self, model: str, values: dict) -> int:
        return self.call(model, "create", vals_list=[values])[0]

    def call_records(self, model: str, method: str, ids: list[int], **kwargs):
        return self.call(model, method, ids=ids, **kwargs)


class PiERPClient:
    def __init__(self, url: str, username: str, password: str):
        raise NotImplementedError("Pi ERP connectivity is not implemented")


class ERPImplementationAgent:
    TOOL_REGISTRY_VERSION = "1.0"
    SAFE_TOOLS = {
        "inspect_instance",
        "installed_modules",
        "inspect_company",
        "inspect_users",
        "inspect_odoo_schema",
        "inspect_views",
        "inspect_access",
        "inspect_master_data",
        "list_directory",
        "read_file",
        "check_deployment_status",
        # Workspace file writes are git-backed and fully reversible — auto-approve
        "create_directory",
        "write_file",
        "patch_file",
    }
    RISK_CLASSES = {
        "create_directory": "C",
        "write_file": "C",
        "update_company_contact": "D",
        "configure_sales": "C",
        "configure_purchase": "C",
        "configure_inventory": "D",
        "create_partner": "C",
        "create_product": "C",
        "create_quotation": "C",
        "create_rfq": "C",
        "create_crm_lead": "C",
        "create_draft_invoice": "D",
        "package_module": "C",
        "execute_deployment": "D",
    }
    TOOL_POLICIES: dict[str, dict[str, str]] = {}

    def __init__(
        self,
        client: OdooClient | OdooJSON2Client,
        workspace_slug: str,
        checkpointer,
        llm_model: str = "gpt-4o-mini",
        api_key: str | None = None,
        base_url: str | None = None,
        request_timeout: int = 120,
        max_output_tokens: int = 16000,
        project_id: int | None = None,
        requested_by_id: int | None = None,
        instance_id: int | None = None,
    ):
        self.client = client
        self.workspace = Workspace(workspace_slug)
        self.project_id = project_id
        self.requested_by_id = requested_by_id
        self.instance_id = instance_id
        self.usage = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        self.activity_events: list[tuple[str, dict]] = []
        kwargs: dict[str, Any] = {
            "model": llm_model,
            "temperature": 0,
            "streaming": True,
            "parallel_tool_calls": False,
            "timeout": request_timeout,
            "max_tokens": max_output_tokens,
        }
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        llm = ChatOpenAI(**kwargs)

        @tool
        def inspect_instance() -> str:
            """Return the connected Odoo version."""
            return json.dumps(self.client.version())

        @tool
        def installed_modules() -> str:
            """List installed Odoo modules."""
            return json.dumps(
                self.client.search_read(
                    "ir.module.module", [("state", "=", "installed")], ["name", "shortdesc"], 200
                )
            )

        @tool
        def inspect_company() -> str:
            """Read the main company's public contact details."""
            return json.dumps(
                self.client.search_read("res.company", [], ["name", "email", "phone", "currency_id"], 1)
            )

        @tool
        def inspect_users() -> str:
            """Read a limited list of Odoo users."""
            return json.dumps(self.client.search_read("res.users", [], ["name", "login"], 10))

        @tool
        def inspect_odoo_schema(model_names: list[str]) -> str:
            """Inspect field names and types for Odoo models. Returns relational fields first, then up to 40 scalar fields - enough for scaffolding."""
            result = {}
            for model_name in model_names:
                models = self.client.search_read("ir.model", [("model", "=", model_name)], ["id", "name", "model"], 1)
                if not models:
                    result[model_name] = "Model not found"
                    continue
                # Relational fields first (always include) — these define the model's relationships
                relational = self.client.search_read(
                    "ir.model.fields",
                    [("model", "=", model_name), ("ttype", "in", ["many2one", "one2many", "many2many"])],
                    ["name", "ttype", "relation"], 50,
                )
                # Top scalar fields for field name awareness
                scalar = self.client.search_read(
                    "ir.model.fields",
                    [("model", "=", model_name), ("ttype", "not in", ["many2one", "one2many", "many2many"])],
                    ["name", "ttype"], 40,
                )
                result[model_name] = {"model": models[0], "relational_fields": relational, "scalar_fields": scalar}
            return json.dumps(result)

        @tool
        def inspect_views(model_name: str) -> str:
            """List base views and window actions registered for an Odoo model. Returns only top-level (non-inherited) views."""
            views = self.client.search_read(
                "ir.ui.view",
                [("model", "=", model_name), ("inherit_id", "=", False), ("active", "=", True)],
                ["name", "type", "id"],
                20
            )
            actions = self.client.search_read("ir.actions.act_window", [("res_model", "=", model_name)], ["name", "view_mode"], 10)
            return json.dumps({"base_views": views, "actions": actions})

        @tool
        def inspect_access(model_name: str) -> str:
            """Inspect access-control and record-rule metadata for a model."""
            models = self.client.search_read("ir.model", [("model", "=", model_name)], ["id"], 1)
            if not models:
                return "Model not found"
            model_id = models[0]["id"]
            access = self.client.search_read("ir.model.access", [("model_id", "=", model_id)], ["name", "group_id", "perm_read", "perm_write", "perm_create", "perm_unlink"], 100)
            rules = self.client.search_read("ir.rule", [("model_id", "=", model_id)], ["name", "groups", "domain_force", "perm_read", "perm_write", "perm_create", "perm_unlink"], 100)
            return json.dumps({"access": access, "rules": rules})

        @tool
        def inspect_master_data(data_type: str, query: str = "", limit: int = 25) -> str:
            """Search an allowlisted master-data type: partners, products, taxes, warehouses, journals, payment_terms, currencies, or units."""
            registry = {
                "partners": ("res.partner", ["name", "email", "phone", "vat"]),
                "products": ("product.template", ["name", "default_code", "type", "list_price"]),
                "taxes": ("account.tax", ["name", "amount", "type_tax_use", "company_id"]),
                "warehouses": ("stock.warehouse", ["name", "code", "company_id"]),
                "journals": ("account.journal", ["name", "code", "type", "company_id"]),
                "payment_terms": ("account.payment.term", ["name", "active", "company_id"]),
                "currencies": ("res.currency", ["name", "symbol", "active"]),
                "units": ("uom.uom", ["name", "category_id", "factor", "active"]),
            }
            if data_type not in registry:
                return "Unsupported master-data type"
            model, fields = registry[data_type]
            domain = [("name", "ilike", query)] if query else []
            return json.dumps(self.client.search_read(model, domain, fields, min(max(limit, 1), 100)))

        @tool
        def list_directory(path: str = "") -> str:
            """List a directory inside this project's workspace."""
            return json.dumps(self.workspace.list_directory(path))

        @tool
        def read_file(path: str) -> str:
            """Read a UTF-8 text file inside this project's workspace."""
            return self.workspace.read_file(path)

        @tool
        def create_directory(path: str) -> str:
            """Create a directory inside this project's workspace after approval."""
            target = self.workspace.resolve(path)
            target.mkdir(parents=True, exist_ok=True)
            return f"Created directory {path}"

        @tool
        def write_file(path: str, content: str) -> str:
            """Atomically write a UTF-8 file inside this project's workspace. Validates Python and XML syntax before writing — returns an error string (not an exception) if validation fails so you can fix and retry."""
            import ast as _ast
            import xml.etree.ElementTree as _ET
            if path.endswith(".py"):
                try:
                    _ast.parse(content, filename=path)
                except SyntaxError as exc:
                    return f"SYNTAX_ERROR in {path}: {exc}. Fix the code and call write_file again."
            elif path.endswith(".xml"):
                try:
                    _ET.fromstring(content) if not content.strip().startswith("<?xml") else _ET.fromstring(content.split("\n", 1)[-1])
                except _ET.ParseError as exc:
                    return f"XML_ERROR in {path}: {exc}. Fix the XML and call write_file again."
            return self.workspace.write_file(path, content)

        @tool
        def patch_file(path: str, old_str: str, new_str: str) -> str:
            """Replace old_str with new_str in an existing workspace file. Use for small targeted edits instead of rewriting the whole file. Returns an error if old_str is not found or appears more than once."""
            current = self.workspace.read_file(path)
            count = current.count(old_str)
            if count == 0:
                return f"PATCH_ERROR: old_str not found in {path}. Use read_file to check the current content."
            if count > 1:
                return f"PATCH_ERROR: old_str appears {count} times in {path}. Make old_str more specific."
            return self.workspace.write_file(path, current.replace(old_str, new_str, 1))

        @tool
        def update_company_contact(company_id: int, email: str, phone: str | None = None) -> str:
            """Update only a company's email and optional phone after approval."""
            updated = self.client.update_company_contact(company_id, email, phone)
            return "Company contact updated" if updated else "Odoo did not update the company"

        def create_record(model: str, values: dict) -> int:
            return self.client.create(model, values)

        def ensure_fields(model: str, values: dict) -> None:
            metadata = self.client.search_read(
                "ir.model.fields", [("model", "=", model), ("name", "in", list(values))], ["name"], 100
            )
            available = {row["name"] for row in metadata}
            missing = set(values) - available
            if missing:
                raise ValueError(f"Unsupported fields for {model}: {', '.join(sorted(missing))}")

        def validated_lines(lines: list[dict], quantity_field: str) -> list:
            if not lines:
                raise ValueError("At least one line is required")
            result = []
            for line in lines:
                unknown = set(line) - {"product_id", "quantity", "unit_price"}
                if unknown or "product_id" not in line or "quantity" not in line:
                    raise ValueError("Lines accept only product_id, quantity, and optional unit_price")
                quantity = float(line["quantity"])
                if quantity <= 0:
                    raise ValueError("Line quantity must be positive")
                result.append((0, 0, {
                    "product_id": int(line["product_id"]),
                    quantity_field: quantity,
                    "price_unit": float(line.get("unit_price", 0)),
                }))
            return result

        @tool
        def configure_sales(quotation_validity_days: int) -> str:
            """Set the default quotation validity in days after approval."""
            values = {"quotation_validity_days": quotation_validity_days}
            ensure_fields("res.config.settings", values)
            settings_id = create_record("res.config.settings", values)
            self._call_records("res.config.settings", "execute", [settings_id])
            return "Sales settings updated"

        @tool
        def configure_purchase(purchase_order_approval: bool) -> str:
            """Enable or disable purchase-order approval after approval."""
            values = {"po_order_approval": purchase_order_approval}
            ensure_fields("res.config.settings", values)
            settings_id = create_record("res.config.settings", values)
            self._call_records("res.config.settings", "execute", [settings_id])
            return "Purchase settings updated"

        @tool
        def configure_inventory(multi_locations: bool) -> str:
            """Enable or disable multi-location inventory after higher-risk approval."""
            values = {"group_stock_multi_locations": multi_locations}
            ensure_fields("res.config.settings", values)
            settings_id = create_record("res.config.settings", values)
            self._call_records("res.config.settings", "execute", [settings_id])
            return "Inventory settings updated"

        @tool
        def create_partner(name: str, email: str | None = None, phone: str | None = None, vat: str | None = None) -> str:
            """Create a customer/vendor master record after duplicate review and approval."""
            duplicates = self.client.search_read("res.partner", [("name", "=ilike", name)], ["id", "name", "email", "vat"], 10)
            if duplicates:
                return json.dumps({"created": False, "duplicates": duplicates})
            values = {"name": name, "email": email, "phone": phone, "vat": vat}
            return json.dumps({"created": True, "id": create_record("res.partner", {k: v for k, v in values.items() if v})})

        @tool
        def create_product(name: str, default_code: str | None = None, list_price: float = 0) -> str:
            """Create a product master record after duplicate review and approval."""
            domain = [("default_code", "=", default_code)] if default_code else [("name", "=ilike", name)]
            duplicates = self.client.search_read("product.template", domain, ["id", "name", "default_code"], 10)
            if duplicates:
                return json.dumps({"created": False, "duplicates": duplicates})
            return json.dumps({"created": True, "id": create_record("product.template", {"name": name, "default_code": default_code, "list_price": list_price})})

        @tool
        def create_quotation(partner_id: int, lines: list[dict]) -> str:
            """Create a draft sales quotation after approval. Lines contain product_id, quantity, and optional unit_price."""
            return json.dumps({"id": create_record("sale.order", {"partner_id": partner_id, "order_line": validated_lines(lines, "product_uom_qty")}), "state": "draft"})

        @tool
        def create_rfq(partner_id: int, lines: list[dict]) -> str:
            """Create a draft purchase RFQ after approval. Lines contain product_id, quantity, and optional unit_price."""
            purchase_lines = validated_lines(lines, "product_qty")
            return json.dumps({"id": create_record("purchase.order", {"partner_id": partner_id, "order_line": purchase_lines}), "state": "draft"})

        @tool
        def create_crm_lead(name: str, partner_id: int | None = None, email_from: str | None = None) -> str:
            """Create a draft CRM lead after approval."""
            values = {"name": name, "partner_id": partner_id, "email_from": email_from, "type": "lead"}
            return json.dumps({"id": create_record("crm.lead", {k: v for k, v in values.items() if v is not None}), "state": "draft"})

        @tool
        def create_draft_invoice(partner_id: int, invoice_type: str, lines: list[dict]) -> str:
            """Create an unposted customer invoice or vendor bill. invoice_type is out_invoice or in_invoice."""
            if invoice_type not in {"out_invoice", "in_invoice"}:
                return "Unsupported invoice type"
            invoice_lines = validated_lines(lines, "quantity")
            return json.dumps({"id": create_record("account.move", {"partner_id": partner_id, "move_type": invoice_type, "invoice_line_ids": invoice_lines}), "state": "draft"})

        @tool
        def package_module(name: str, version: str, path: str) -> str:
            """Package a custom module in the workspace into a versioned artifact and run automated validations after approval."""
            module_path = self.workspace.resolve(path, must_exist=True)
            commit_hash = self.workspace.commit(f"Package {name} {version}")
            report = validation.validate_module(module_path)
            archive_digest = hashlib.sha256(validation.package_module(module_path)).hexdigest()
            
            with SessionLocal() as db:
                artifact = models.Artifact(
                    project_id=self.project_id,
                    artifact_type="odoo_module",
                    name=name,
                    version=version,
                    commit_hash=commit_hash,
                    digest=archive_digest,
                    path=path,
                    status="validated" if report["passed"] else "failed_validation",
                    created_by_id=self.requested_by_id,
                )
                db.add(artifact)
                db.flush()
                val_run = models.ValidationRun(
                    project_id=self.project_id,
                    artifact_id=artifact.id,
                    status="passed" if report["passed"] else "failed",
                    report=report,
                    finished_at=datetime.now(timezone.utc),
                )
                db.add(val_run)
                db.commit()
                
                return json.dumps({
                    "artifact_id": artifact.id,
                    "status": artifact.status,
                    "validation": report,
                })

        @tool
        def execute_deployment(artifact_id: str, environment: str) -> str:
            """Trigger a deployment of an artifact to an environment (staging or production) after approval."""
            with SessionLocal() as db:
                artifact = db.get(models.Artifact, artifact_id)
                if not artifact or artifact.project_id != self.project_id:
                    return "Artifact not found"
                val = db.query(models.ValidationRun).filter(
                    models.ValidationRun.artifact_id == artifact.id, models.ValidationRun.status == "passed"
                ).order_by(models.ValidationRun.created_at.desc()).first()
                if not val:
                    return "Cannot deploy artifact without a passed validation run"
                
                deploy = models.Deployment(
                    project_id=self.project_id,
                    instance_id=self.instance_id,
                    artifact_id=artifact.id,
                    validation_id=val.id,
                    environment=environment,
                    status="queued",
                    requested_by_id=self.requested_by_id,
                )
                db.add(deploy)
                db.commit()
                
                try:
                    deployment.execute_deployment(deploy.id)
                    db.refresh(deploy)
                    return json.dumps({"deployment_id": deploy.id, "status": deploy.status})
                except Exception as exc:
                    return json.dumps({"deployment_id": deploy.id, "status": "failed", "error": str(exc)})

        @tool
        def check_deployment_status(deployment_id: str) -> str:
            """Check the status of a deployment."""
            with SessionLocal() as db:
                deploy = db.get(models.Deployment, deployment_id)
                if not deploy or deploy.project_id != self.project_id:
                    return "Deployment not found"
                
                if deploy.status in {"queued", "deploying", "executing"}:
                    instance = db.get(models.Instance, deploy.instance_id)
                    if instance.hosting_type != "odoo_sh":
                        try:
                            deployment.refresh_bridge_deployment(deploy, instance)
                            db.commit()
                        except Exception as exc:
                            return f"Failed to refresh deployment: {exc}"
                
                return json.dumps({
                    "status": deploy.status,
                    "logs": deploy.logs[-2000:] if deploy.logs else "",
                })

        self.tools = [
            inspect_instance,
            installed_modules,
            inspect_company,
            inspect_users,
            inspect_odoo_schema,
            inspect_views,
            inspect_access,
            inspect_master_data,
            list_directory,
            read_file,
            create_directory,
            write_file,
            patch_file,
            update_company_contact,
            configure_sales,
            configure_purchase,
            configure_inventory,
            create_partner,
            create_product,
            create_quotation,
            create_rfq,
            create_crm_lead,
            create_draft_invoice,
            package_module,
            execute_deployment,
            check_deployment_status,
        ]
        kb_path = Path(__file__).parent.parent / "skills" / "odoo19-dev" / "Odoo19_Dev_Customization_KB.md"
        self._kb_path = kb_path  # stored for the read_knowledge_base tool below

        @tool
        def read_knowledge_base() -> str:
            """Read the Odoo 19 technical reference (ORM patterns, view syntax, manifest format, security CSV). Call this ONCE before writing any module code if you are uncertain about Odoo 19 conventions."""
            return kb_path.read_text(encoding="utf-8") if kb_path.exists() else "Knowledge base not found."

        self.tools.append(read_knowledge_base)
        # Add to SAFE_TOOLS so it doesn't require approval
        ERPImplementationAgent.SAFE_TOOLS.add("read_knowledge_base")

        prompt = SystemMessage(
            content=(
                "You are an expert autonomous Odoo 19 ERP implementation agent. "
                "Every ERP schema fact must come from a tool result. "
                "Use only the registered typed tools.\n\n"
                "When requested to build, customize, or scaffold a module:\n"
                "1. Inspect the live database schema (inspect_odoo_schema / inspect_views) ONCE to verify model names and fields. Do NOT repeat if you already have results in this session.\n"
                "2. If uncertain about Odoo 19 syntax (ORM fields, view arch, manifest format), call read_knowledge_base ONCE.\n"
                "3. Create directories with create_directory, then write files with write_file.\n"
                "4. If write_file returns SYNTAX_ERROR or XML_ERROR, fix the content and call write_file again immediately.\n"
                "5. After each write_file call, call read_file on the same path to verify the file was written correctly.\n"
                "6. After all files are written, call package_module to validate, then summarise what was built.\n"
                "7. Always call exactly one tool at a time. Never use placeholders in generated code."
            )
        )
        self.executor = create_react_agent(
            llm,
            self.tools,
            prompt=prompt,
            checkpointer=checkpointer,
            interrupt_before=["tools"],
        )

    def _call_records(self, model: str, method: str, ids: list[int]):
        if isinstance(self.client, OdooJSON2Client):
            return self.client.call_records(model, method, ids)
        return self.client.call(model, method, ids)

    @staticmethod
    def _history(interactions: list) -> list:
        messages = []
        for interaction in interactions:
            if interaction.role == "user":
                messages.append(HumanMessage(content=interaction.content))
            elif interaction.role == "agent":
                messages.append(AIMessage(content=interaction.content))
        return messages

    async def stream(self, prompt: str | None, thread_id: str, interactions: list | None = None, reject=False):
        config = {"configurable": {"thread_id": thread_id}}
        if prompt is not None:
            existing = await self.executor.aget_state(config)
            messages = [] if existing.values.get("messages") else self._history(interactions or [])
            messages.append(HumanMessage(content=prompt))
            input_data = {"messages": messages}
        else:
            if reject:
                state = await self.executor.aget_state(config)
                last = state.values.get("messages", [])[-1]
                calls = getattr(last, "tool_calls", [])
                if len(calls) != 1:
                    raise ValueError("Pending checkpoint does not contain one tool call")
                call = calls[0]
                await self.executor.aupdate_state(
                    config,
                    {"messages": [ToolMessage(tool_call_id=call["id"], name=call["name"], content="User rejected this action.")]},
                    as_node="tools",
                )
            input_data = None

        while True:
            async for event in self.executor.astream_events(input_data, config, version="v2"):
                if event["event"] == "on_chat_model_stream":
                    content = event["data"]["chunk"].content
                    if isinstance(content, str) and content:
                        yield content
                elif event["event"] == "on_chat_model_end":
                    output = event.get("data", {}).get("output")
                    usage = getattr(output, "usage_metadata", None) or {}
                    self.usage["input_tokens"] += int(usage.get("input_tokens", 0) or 0)
                    self.usage["output_tokens"] += int(usage.get("output_tokens", 0) or 0)
                    metadata = getattr(output, "response_metadata", None) or {}
                    self.usage["cost_usd"] += float(metadata.get("cost", 0) or 0)
                elif event["event"] == "on_tool_start":
                    self.activity_events.append(("tool.started", {
                        "tool": event.get("name", "unknown"), "arguments": event.get("data", {}).get("input", {}),
                    }))
                elif event["event"] == "on_tool_end":
                    output = str(event.get("data", {}).get("output", ""))
                    self.activity_events.append(("tool.completed", {
                        "tool": event.get("name", "unknown"), "result": output[:10_000], "truncated": len(output) > 10_000,
                    }))
            state = await self.executor.aget_state(config)
            if not state.next:
                return
            if "tools" not in state.next:
                input_data = None
                continue
            call = self._single_pending_call(state)
            if call["name"] not in self.SAFE_TOOLS:
                return
            input_data = None

    async def pending_call(self, thread_id: str) -> dict | None:
        state = await self.executor.aget_state({"configurable": {"thread_id": thread_id}})
        if "tools" not in state.next:
            return None
        call = self._single_pending_call(state)
        if call["name"] in self.SAFE_TOOLS:
            return None
        return call

    @staticmethod
    def _single_pending_call(state) -> dict:
        last = state.values.get("messages", [])[-1]
        calls = getattr(last, "tool_calls", [])
        if len(calls) != 1:
            raise ValueError("The agent must request exactly one tool at a time")
        return calls[0]

    def preview(self, call: dict) -> dict:
        name, args = call["name"], call["args"]
        if name == "write_file":
            return self.workspace.write_preview(args["path"], args["content"])
        if name == "create_directory":
            self.workspace.resolve(args["path"])
            return {"path": args["path"], "operation": "create directory"}
        if name == "update_company_contact":
            return {
                "operation": "update company contact",
                "company_id": args["company_id"],
                "email": args["email"],
                "phone": args.get("phone"),
            }
        if name in self.RISK_CLASSES:
            return {
                "operation": name.replace("_", " "),
                "arguments": args,
                "risk_class": self.RISK_CLASSES[name],
            }
        raise ValueError("Unsupported approval tool")

    def drain_activity(self) -> list[tuple[str, dict]]:
        events, self.activity_events = self.activity_events, []
        return events


ERPImplementationAgent.TOOL_POLICIES = {
    name: {
        "version": ERPImplementationAgent.TOOL_REGISTRY_VERSION,
        "risk_class": ERPImplementationAgent.RISK_CLASSES.get(name, "A"),
        "approval": "automatic" if name in ERPImplementationAgent.SAFE_TOOLS else "explicit",
        "idempotency": "required" if name not in ERPImplementationAgent.SAFE_TOOLS else "read_only",
    }
    for name in ERPImplementationAgent.SAFE_TOOLS | ERPImplementationAgent.RISK_CLASSES.keys()
}


async def connect_odoo(url: str, db: str, username: str, password: str) -> OdooClient:
    return await asyncio.wait_for(
        asyncio.to_thread(OdooClient, url, db, username, password),
        timeout=10,
    )


async def connect_odoo_json2(url: str, db: str, api_key: str) -> OdooJSON2Client:
    return await asyncio.wait_for(asyncio.to_thread(OdooJSON2Client, url, db, api_key), timeout=10)
