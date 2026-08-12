# Odoo 19 Development & Customization Knowledge Base
### (Structured for RAG / Vector DB Ingestion)

> **Chunking note for ingestion:** Each `##`/`###` section below is written to be self-contained — it repeats the topic and version context so it still makes sense in isolation after chunking. Recommended strategy: chunk on `##`/`###` boundaries, keep code blocks intact with their parent section (don't split mid-block), target ~300–500 tokens per chunk, and attach the `tags:` line of each section as metadata (module, topic, risk_class) for filtered retrieval by your ERP Implementation Agent's tool layer. Each section can be embedded with a prefix like `"Odoo 19 development — {topic}: "` for better retrieval quality.

---

## 1. Odoo 19 Release Overview
**tags:** overview, versioning, release

Odoo 19.0 was officially released in September 2025, followed by minor patch releases 19.1 (January 2026), 19.2, and 19.3 (May 2026). Odoo 19 is built on **PostgreSQL 17** and **Python 3.12** as baseline requirements — both server and database must meet these versions before installation or upgrade. Headline product changes in Odoo 19 include AI agents capable of creating records autonomously from uploaded documents, an offline-first mobile mode, a redesigned Manufacturing Kanban view, two new modules (Equity and ESG), 100+ industry starter packages, native dark mode, and roughly a 40% performance improvement across the web client. For an Odoo Implementation Agent, this means: verify target instance is on PostgreSQL 17 / Python 3.12 before any migration or code-generation task, and expect custom modules originally written for 17.x or 18.x to need review before they run cleanly on 19.

---

## 2. Module Directory Structure
**tags:** module_structure, scaffolding, fundamentals

A standard Odoo 19 custom module follows this directory layout:

```
my_module/
├── __init__.py
├── __manifest__.py
├── models/
│   ├── __init__.py
│   └── my_model.py
├── views/
│   └── my_model_views.xml
├── security/
│   ├── ir.model.access.csv
│   └── my_module_security.xml
├── data/
│   └── my_module_data.xml
├── demo/
│   └── my_module_demo.xml
├── static/
│   ├── description/
│   │   └── icon.png
│   └── src/
│       ├── js/
│       ├── scss/
│       └── xml/
├── report/
│   └── my_module_report.xml
├── wizard/
│   └── my_wizard.py
├── controllers/
│   └── main.py
└── tests/
    ├── __init__.py
    └── test_my_module.py
```

Scaffolding a new module can be done via the CLI: `odoo-bin scaffold my_module /path/to/addons`. This is optional but avoids missing boilerplate files. Every new addon should be verified in a development branch/staging instance before merging to production, especially on Odoo.sh-managed deployments.

---

## 3. The `__manifest__.py` File
**tags:** manifest, module_structure, dependencies

Every Odoo 19 module requires a `__manifest__.py` describing metadata:

```python
{
    'name': "My Module",
    'version': '19.0.1.0.0',
    'summary': "Short one-line description",
    'description': """Longer multi-line description""",
    'author': "Primacy Infotech",
    'website': "https://primacyinfotech.com",
    'category': "Sales",
    'depends': ['base', 'sale', 'mail'],
    'data': [
        'security/ir.model.access.csv',
        'security/my_module_security.xml',
        'views/my_model_views.xml',
        'data/my_module_data.xml',
    ],
    'demo': [
        'demo/my_module_demo.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'my_module/static/src/js/*.js',
            'my_module/static/src/scss/*.scss',
            'my_module/static/src/xml/*.xml',
        ],
    },
    'installable': True,
    'application': True,
    'auto_install': False,
    'license': 'LGPL-3',
}
```

Key notes: the `version` string's first two segments should match the Odoo version (`19.0`); bumping the version number in the manifest and redeploying triggers an automatic module update on Odoo.sh; the `assets` key replaces the older `qweb`/inline `<script>` asset declarations for JS/SCSS/XML bundling in OWL-based modules.

---

## 4. Defining Models (ORM Basics)
**tags:** orm, models, python, fundamentals

Odoo 19 models are Python classes inheriting from `odoo.models.Model` (or `TransientModel` for wizards, `AbstractModel` for mixins):

```python
from odoo import api, fields, models


class MyModel(models.Model):
    _name = 'my.model'
    _description = 'My Model'
    _order = 'sequence, id'
    _rec_name = 'name'

    name = fields.Char(string="Name", required=True, tracking=True)
    sequence = fields.Integer(default=10)
    partner_id = fields.Many2one('res.partner', string="Customer")
    line_ids = fields.One2many('my.model.line', 'model_id', string="Lines")
    tag_ids = fields.Many2many('my.model.tag', string="Tags")
    amount_total = fields.Monetary(compute='_compute_amount_total', store=True)
    currency_id = fields.Many2one(related='partner_id.currency_id')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('confirmed', 'Confirmed'),
        ('done', 'Done'),
    ], default='draft', tracking=True)

    @api.depends('line_ids.amount')
    def _compute_amount_total(self):
        for record in self:
            record.amount_total = sum(record.line_ids.mapped('amount'))
```

`_inherit` is used to extend an existing model (adding fields/methods to e.g. `sale.order`); `_inherit` combined with a new `_name` creates a new model that copies the parent's structure (delegation inheritance is rare — prefer classic inheritance via `_inherit` alone for extension). Always add `_description` — Odoo 19 emits warnings/errors for models missing it in stricter dev-mode logging.

---

## 5. ORM API — Key Decorators & Patterns
**tags:** orm, decorators, python

Common decorators used throughout Odoo 19 model code:

- `@api.depends('field1', 'field2')` — declares a compute method's dependencies for automatic recomputation.
- `@api.onchange('field')` — triggers client-side field updates when a form field changes (only for form views; does not run on server-side writes).
- `@api.constrains('field')` — Python-level validation raising `ValidationError` on `create`/`write`.
- `@api.model` — marks a method that doesn't operate on a specific recordset (called on the model class, e.g. factory-style methods).
- `@api.model_create_multi` — recommended for `create()` overrides that must support batch creation of multiple records in one call.
- `@api.private` — **new in Odoo 19 (master/19.x)**, marks Python methods as internal (not exposed for RPC/external calls), distinguishing public API methods from internal implementation details.

Example combining several:

```python
@api.model_create_multi
def create(self, vals_list):
    records = super().create(vals_list)
    records._notify_creation()
    return records

@api.constrains('amount_total')
def _check_amount_total(self):
    for record in self:
        if record.amount_total < 0:
            raise ValidationError("Total amount cannot be negative.")
```

---

## 6. Odoo 19 ORM API Changes (vs. Odoo 17/18)
**tags:** orm, changelog, migration, breaking_changes

These are the ORM-level changes an Implementation/Development Agent must account for when generating or reviewing code targeting Odoo 19:

- **`name_get()` is deprecated.** Use the computed field `display_name` (override `_compute_display_name()`) instead of overriding `name_get()`. Legacy `name_get()` overrides should be migrated to `_compute_display_name()`.
- **`_read_group()` has a new signature** — refactored to combine searching and reading into a minimal number of SQL queries; new methods `search_fetch()` and `fetch()` take advantage of this combination. The top-level `read_group()` is deprecated in favor of `_read_group()` for backend use and `formatted_read_group()` as the public API.
- **`group_operator` renamed to `aggregator`** on `Field` definitions (e.g. `fields.Float(aggregator="sum")`).
- **`column_format` and `deprecated` attributes of `Field` are removed.**
- **`args` parameter renamed to `domain`** for `search()`, `search_count()`, and `_search()`.
- **The `limit` attribute of `One2many`/`Many2many` is removed.**
- **`fields_get_keys()` and `get_xml_id()` on `Model` are deprecated.**
- **`_sequence` attribute of `Model` is removed** — Odoo now lets PostgreSQL use the default primary-key sequence.
- **New SQL wrapper object** (`odoo.tools.sql.SQL`) introduced for safer SQL composition, replacing raw string concatenation in ORM internals — custom code executing raw SQL should adopt this pattern to avoid injection risk and stay consistent with core.
- **New access-rights API**: `check_access()`, `has_access()`, and `_filtered_access()` combine access-right and record-rule checks in one call (superseding scattered `check_access_rights()` + `check_access_rule()` patterns).
- **Field-level index control**: developers can specify PostgreSQL index types via the `index` property of `odoo.fields.Field` (e.g. `index='btree_not_null'`).
- **`inselect` internal domain operator removed** — use a `Query` or `SQL` object instead.
- **Native PEP-420 namespace packages** for Odoo modules (ongoing in master/19.x branches) — affects how addons paths and namespace packages resolve.

Practical guidance for the agent: when generating new Odoo 19 model code, never emit `name_get()` overrides; always prefer `_compute_display_name()`. When reviewing legacy (17/18) custom modules for migration, flag every `name_get`, `args=`, `group_operator`, and raw `read_group()` usage as a required change.

---

## 7. Views — XML View Changes in Odoo 19
**tags:** views, xml, ui, breaking_changes

Key backend-visible view changes developers must apply when writing or migrating XML views for Odoo 19:

- **`attrs` attribute is fully removed.** Conditional visibility/readonly/required must be written as direct inline attributes using Python-like domain expressions:
  ```xml
  <!-- Old (pre-17, no longer valid) -->
  <field name="partner_id" attrs="{'invisible': [('state','=','draft')]}"/>

  <!-- Odoo 19 -->
  <field name="partner_id" invisible="state == 'draft'"/>
  <field name="amount" readonly="state != 'draft'"/>
  <field name="reference" required="state == 'confirmed'"/>
  ```
- **`<tree>` view tag is renamed to `<list>`.** Any custom view, inherited view, or XPath expression targeting `//tree` must be updated to `//list`, and view type strings `'tree'` become `'list'` in Python (e.g. `context.get('list_view_ref')`).
- View inheritance (`<xpath expr="..." position="...">`) mechanics are unchanged, but XPath expressions that reference removed/renamed stock nodes (renamed OWL components, `<tree>` → `<list>`) will silently render empty rather than error — this is a common silent-failure migration bug that must be checked manually or via automated view-render tests.

---

## 8. Security — Access Rights & Record Rules
**tags:** security, access_rights, record_rules, groups

Security in Odoo 19 follows the same two-layer model as prior versions, unchanged in mechanics but now checked internally via the unified `check_access()`/`has_access()` API (see ORM changes above).

**`ir.model.access.csv`** (model-level CRUD permissions):
```csv
id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink
access_my_model_user,my.model.user,model_my_model,base.group_user,1,1,1,0
access_my_model_manager,my.model.manager,model_my_model,my_module.group_my_module_manager,1,1,1,1
```

**Record rules** (row-level filtering) defined in XML:
```xml
<record id="my_model_rule_own" model="ir.rule">
    <field name="name">My Model: own records only</field>
    <field name="model_id" ref="model_my_model"/>
    <field name="domain_force">[('user_id','=',user.id)]</field>
    <field name="groups" eval="[(4, ref('base.group_user'))]"/>
</record>
```

**Security groups**:
```xml
<record id="group_my_module_manager" model="res.groups">
    <field name="name">My Module / Manager</field>
    <field name="category_id" ref="base.module_category_operations"/>
    <field name="implied_ids" eval="[(4, ref('base.group_user'))]"/>
</record>
```

Design principle for AI-assisted or AI-executed actions: the AI agent's effective permission must always be the *intersection* of the acting user's ERP permissions, the product policy, the agent's tool permission, and the action's risk classification — never a superset. Never generate code or configuration that grants the AI service account broader access than the requesting user already has.

---

## 9. QWeb Templates & the Reporting Engine
**tags:** qweb, reports, templates

Odoo 19 ships a refreshed QWeb rendering engine used both for backend PDF/HTML reports and (via OWL 3) for web client templates. Key points:

- Report templates (`<template>` records rendered via `ir.actions.report`) still use QWeb directive syntax (`t-if`, `t-foreach`, `t-field`, `t-esc`/`t-out`) for **server-side report rendering** — this is distinct from OWL 3 client templates.
- For **OWL 3 client-side component templates**, `t-esc` is deprecated in favor of `t-out` (semantics are identical for escaped string output — `t-out` is the forward-compatible directive). Automated tooling (an ESLint rule or pre-commit grep) should flag remaining `t-esc` usage in `static/src/**/*.xml` files.
- Report actions are still declared the same way:
  ```xml
  <record id="action_report_my_model" model="ir.actions.report">
      <field name="name">My Model Report</field>
      <field name="model">my.model</field>
      <field name="report_type">qweb-pdf</field>
      <field name="report_name">my_module.report_my_model</field>
      <field name="print_report_name">'My Report - %s' % (object.name)</field>
      <field name="binding_model_id" ref="model_my_model"/>
      <field name="binding_type">report</field>
  </record>
  ```

---

## 10. OWL 3 — Frontend Component Framework
**tags:** owl, javascript, frontend, breaking_changes

Odoo 19 **completes the migration from the legacy Widget/jQuery system to OWL 3** (Odoo Web Library). Any code still using `Widget.extend()`, QWeb2 templates, or jQuery-based event binding from Odoo 16-era modules will not run. OWL 3 is a full component framework (comparable in philosophy to Vue/React) with a fine-grained reactive state system, declarative templates, lifecycle hooks, and a service-based dependency-injection layer.

**Basic OWL 3 component:**
```javascript
/** @odoo-module **/
import { Component, onWillStart, onMounted, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { proxy } from "@odoo/owl";

class MyDashboardWidget extends Component {
    static template = "my_module.MyDashboardWidget";
    static props = {
        title: { type: String },
        recordId: { type: Number, optional: true },
    };

    setup() {
        this.orm = useService("orm");
        this.state = proxy({ value: 0, loading: true });

        onWillStart(async () => {
            const data = await this.orm.call("my.model", "read", [[this.props.recordId]]);
            this.state.value = data[0].amount_total;
            this.state.loading = false;
        });

        this._interval = null;
        onMounted(() => {
            this._interval = setInterval(() => this._refresh(), 30000);
        });
        onWillUnmount(() => {
            if (this._interval) clearInterval(this._interval);
        });
    }
}
registry.category("actions").add("my_module.dashboard_widget", MyDashboardWidget);
```

**Key OWL 2 → OWL 3 migration changes:**
- `useState()` is replaced by `proxy()` (import from `@odoo/owl`); the underlying implementation now uses signals rather than a Proxy-based reactive object, but the call pattern is a near drop-in replacement.
- Class field syntax `static template = '...'; willStart() {...}` (older lifecycle-as-method style) is replaced by `setup()` with hook functions: `onWillStart(async () => {...})`, `onMounted()`, `onWillUnmount()`, `onPatched()`.
- `static props` should be declared as a **validated object schema**, not a plain array of prop names — this enables runtime prop-type checking in dev mode.
- Components that register listeners/intervals/subscriptions in `onMounted` **must** tear them down in `onWillUnmount` — OWL does not auto-pair mount/unmount the way React's `useEffect` return function does; failing to clean up causes ghost RPC calls and memory leaks.
- The old `Store` system was removed; state management for cross-component data should be built in user-space on top of the reactivity primitives, or via services.
- Bus event methods changed: `bus.on(...)` → `bus.addEventListener(...)`, `bus.off(...)` → `bus.removeEventListener(...)`.
- `this.props` and `this.env` direct access patterns from very old widget code are removed; use the declared `props` schema and service/env hooks instead.
- Custom field widgets are now registered via the fields registry rather than subclassing `AbstractField`:
  ```javascript
  registry.category("fields").add("my_custom_field", {
      component: MyCustomFieldComponent,
      supportedTypes: ["char", "text"],
      extractProps: ({ attrs }) => ({ placeholder: attrs.placeholder }),
  });
  ```

**Migration risk flag for the Implementation Agent:** stock OWL components were renamed in several core modules between 17→19; any custom `_inherit`/XPath extension of stock QWeb templates targeting a renamed or removed node will silently render empty rather than raise an error. This must be checked by rendering the view/component in a staging environment, not by static code review alone.

---

## 11. Controllers & REST API
**tags:** controllers, api, rest, authentication

Standard HTTP controllers remain largely unchanged in mechanics:

```python
from odoo import http
from odoo.http import request


class MyController(http.Controller):

    @http.route('/my_module/summary', type='json', auth='user', methods=['POST'])
    def get_summary(self, **kwargs):
        records = request.env['my.model'].search([])
        return {'count': len(records), 'total': sum(records.mapped('amount_total'))}
```

Odoo 19 additionally exposes a native **REST API layer following OpenAPI 3.1 specifications**, with auto-generated documentation available at `/api/docs` on a running instance. Authentication for this API layer supports **API keys, OAuth 2.0, and JWT tokens** — a materially different (and more standards-aligned) surface than the classic session-cookie-based controller auth (`auth='user'`/`auth='public'`). For an ERP AI Assistant or Implementation Agent that needs to call Odoo programmatically from an external service (e.g. an MCP connector), prefer the OpenAPI/JWT or API-key route over spoofing session cookies, since it is explicitly designed for external/service-to-service integration and supports scoped, rotatable credentials — aligning with least-privilege connector design.

---

## 12. Development Server & Tooling
**tags:** dev_environment, cli, tooling

Common CLI usage for Odoo 19 development:

```bash
# Run with a specific addons path, install a module, and enable dev tools
python3 odoo-bin -d mydb --addons-path=addons,custom_addons -i my_module --dev=all

# Update (upgrade) a module after code changes
python3 odoo-bin -d mydb -u my_module --stop-after-init

# Run module tests
python3 odoo-bin -d test_db --test-enable -u my_module --stop-after-init
```

The `--dev` flag family (e.g. `--dev=all`, `--dev=reload,qweb,xml`) enables:
- **Hot module replacement (HMR)** for JavaScript and QWeb template changes — the web client reflects changes without a full page reload.
- Detailed SQL query logging and performance profiling, useful for diagnosing slow custom code before it reaches production.

On **Odoo.sh**, the workflow is branch-based: create a development branch (forked from `main`/production), edit code under `~/src/user`, test on the auto-provisioned build, then convert the branch to staging (drag-and-drop or `git merge`) to get a full duplicate of the production database running your changes, and only then merge to production. Modules are **not** auto-installed on staging builds by design (so behavior matches what would happen in production); install manually from the Apps menu, updating the apps list first if the module doesn't yet appear. External Python dependencies for a module should be declared in a `requirements.txt` file at the addon root — system-level (`apt`) packages cannot be installed on Odoo.sh.

---

## 13. Testing Framework
**tags:** testing, qa, unit_tests

Odoo's Python testing framework (`odoo.tests.common`) is unchanged in structure for Odoo 19:

```python
from odoo.tests.common import TransactionCase
from odoo.exceptions import ValidationError


class TestMyModel(TransactionCase):

    def setUp(self):
        super().setUp()
        self.partner = self.env['res.partner'].create({'name': 'Test Partner'})

    def test_amount_total_positive(self):
        record = self.env['my.model'].create({
            'name': 'Test Record',
            'partner_id': self.partner.id,
        })
        with self.assertRaises(ValidationError):
            record.write({'amount_total': -10})
```

Run with `--test-enable -u my_module --stop-after-init`. For an Implementation Agent's "Testing Agent" mode, generated test cases should always run against a staging/test database, never production, and should assert both the happy path and the risk-relevant validation/security constraints (record rules, required approvals) for any Class C/D/E action the module introduces.

---

## 14. Migrating Custom Modules from Odoo 17/18 to 19
**tags:** migration, upgrade, checklist

Checklist an Implementation Agent should apply when reviewing a custom module for Odoo 19 compatibility:

1. **XML views:** replace every `attrs="{...}"` with inline `invisible=`/`readonly=`/`required=` expressions; rename every `<tree>` to `<list>` (including in XPath `expr` targets and Python `view_type` strings).
2. **Python ORM:** remove any `name_get()` override → migrate to `_compute_display_name()`; rename `args` kwargs to `domain`; rename `group_operator` to `aggregator`; remove usage of removed `Field` attributes (`column_format`, `deprecated`, `_sequence`, One2many/Many2many `limit`).
3. **JavaScript/OWL:** migrate `Widget.extend()`/jQuery-based widgets to OWL 3 components; replace `useState` with `proxy`; move lifecycle logic into `setup()` with hooks; replace `t-esc` with `t-out` in client templates; ensure `onWillUnmount` cleanup for anything registered in `onMounted`.
4. **Reports:** verify QWeb report templates render correctly under the refreshed engine; check for any renamed core template nodes referenced via inheritance.
5. **API/integration code:** if the module exposes or calls external APIs, evaluate moving to the native OpenAPI 3.1 REST layer (API key/OAuth2/JWT) instead of legacy session-based calls where appropriate.
6. **Server/infra baseline:** confirm PostgreSQL 17 and Python 3.12 on the target environment before attempting the code migration.
7. **Path strategy:** direct 17→19 migration is supported (via official tooling / OpenUpgrade) but adds materially more data-migration effort than a staged 17→18→19 path; for 5+ custom modules, staged migration is generally lower-risk.
8. **Validation:** install and run automated tests for every custom module in a staging copy before promoting to production; do not trust static review alone for view/template breakage, since silently-empty renders are a known failure mode.

---

## 15. Class-Based Risk Model for AI-Generated/AI-Executed Changes
**tags:** governance, risk_classes, ai_agent_policy

When an Implementation Agent proposes or performs a configuration/code change on an Odoo 19 instance, classify the action before executing it:

| Class | Examples | Agent behavior |
|---|---|---|
| A — Read-only | inspect config, list modules, search records | May run directly |
| B — Low-risk write | create draft record, add internal note | May be auto-allowed per policy |
| C — Business/config change | change a workflow setting, install a low-risk module | Preview + explicit confirmation required |
| D — Structural/financial | schema-affecting migration, accounting config, module uninstall | Explicit confirmation + staging validation required |
| E — Critical/admin | production deploy, delete data, change permissions, install unreviewed third-party modules | Never automatic — requires human review and, for code, passage through the full dev pipeline below |

**Development Agent safety pipeline (Class E code changes):**
```
Requirement → Technical Specification → Code Generation → Static Validation
→ Security Analysis → Test Environment → Automated Tests → Human Review → Deployment Approval
```
No AI-generated code should move directly from prompt to customer production. Production changes should default to a staging clone (Odoo.sh staging branch or equivalent) for validation first.

---

## 16. Notable Odoo 19 Product-Level Additions Relevant to Customization
**tags:** new_features, product, context

These are new-in-19 product capabilities worth knowing when scoping customization/implementation work (not core dev-framework mechanics, but relevant context for gap analysis and configuration recommendations):

- **AI agents** that can create ERP records autonomously from uploaded documents (e.g. auto-drafting bills/leads from a scanned document).
- **Offline-first mobile mode** — the mobile app can operate and queue actions without a live connection.
- **Redesigned Manufacturing Kanban view** and broader Manufacturing module workflow updates (planning through production tracking).
- **Two new modules: Equity and ESG.**
- **100+ industry starter packages** for faster vertical-specific onboarding.
- **Native dark mode** for the web client.
- **~40% general UI performance improvement** attributed to the OWL 3 migration and backend query refactors (see `search_fetch()`/`fetch()` combination in the ORM changelog).
- **Predictive inventory forecasting** using historical consumption patterns for reordering recommendations.
- **AI-driven lead scoring** in the CRM pipeline.

These should inform the Implementation Agent's "Recommend Configuration" and gap-analysis steps — e.g. flagging when a customer's existing Odoo 17/18 customization duplicates functionality now natively available in Odoo 19 (a case where the recommendation should be "adopt the native feature" rather than "port the custom module").

---

## 17. Source References
**tags:** sources

- Odoo 19.0 official developer documentation: `https://www.odoo.com/documentation/19.0/developer.html`
- Odoo 19.0 ORM API changelog: `https://www.odoo.com/documentation/19.0/developer/reference/backend/orm/changelog.html`
- Odoo 19.0 Server Framework 101 tutorial: `https://www.odoo.com/documentation/19.0/developer/tutorials/server_framework_101/02_newapp.html`
- Odoo.sh module creation/branching docs: `https://www.odoo.com/documentation/19.0/administration/odoo_sh/create_module.html`
- OWL framework changelog (odoo/owl GitHub repo)
- Various third-party Odoo 19 migration write-ups covering OWL 3, ORM, and view-layer breaking changes (community sources; verify against official changelog for authoritative behavior before generating production code).
