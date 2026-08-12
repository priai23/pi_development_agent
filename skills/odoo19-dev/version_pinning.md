# Odoo Version Pinning — Hallucination Risk Reference

Purpose: an agent that doesn't know (or doesn't check) which Odoo version it's targeting will
confidently generate syntax that was valid in one version and silently wrong/broken in another.
Confirm the target version before generating or reviewing code. If unstated, ask or inspect
(`ir.module.module` / server info) rather than assume.

## Odoo 17 → 18 → 19: the changes most likely to produce plausible-but-wrong output

### Views (XML)
- `attrs="{...}"` is **removed**. Use inline `invisible=`, `readonly=`, `required=` with a
  Python-like expression: `<field name="x" invisible="state == 'draft'"/>`.
- `<tree>` is renamed `<list>` — including in XPath `expr` targets on inherited views, and in
  Python `view_type` strings/context keys (`list_view_ref` not `tree_view_ref`).
- Inherited views/XPath targeting a renamed or removed stock node **fail silently** (render
  empty) rather than raising an error — do not trust static review alone; render-test in staging.

### ORM / Python
- `name_get()` is **deprecated** — use `_compute_display_name()` / read `display_name` instead.
  Do not generate new `name_get()` overrides for 17+.
- `_read_group()` has a new signature; top-level `read_group()` is deprecated in favor of
  `_read_group()` (backend) / `formatted_read_group()` (public API).
- `group_operator` on `Field` is renamed `aggregator`.
- `args` kwarg on `search()`/`search_count()`/`_search()` is renamed `domain`.
- `column_format` and `deprecated` `Field` attributes are removed.
- `limit` attribute of `One2many`/`Many2many` is removed.
- `fields_get_keys()` and `get_xml_id()` on `Model` are deprecated.
- `_sequence` attribute of `Model` is removed (PostgreSQL default PK sequence is used).
- New unified access API: `check_access()`, `has_access()`, `_filtered_access()` — prefer these
  over separately calling legacy `check_access_rights()` + `check_access_rule()`.
- `inselect` domain operator is removed — use a `Query`/`SQL` object.
- `@api.private` (19.x/master) marks methods not exposed for RPC — useful when generating code
  that should not be externally callable.

### JavaScript / Frontend (OWL)
- Odoo 19 completes the move to **OWL 3**. Anything using `Widget.extend()`, QWeb2 templates, or
  jQuery-based event binding (pre-17 style) will not run.
- OWL 2 → OWL 3: `useState()` → `proxy()`; lifecycle-as-method (`willStart()`) → `setup()` with
  hooks (`onWillStart`, `onMounted`, `onWillUnmount`, `onPatched`); `static props` should be a
  validated object schema, not a plain array; `t-esc` → `t-out` in component templates.
- Anything registered in `onMounted` (intervals, listeners, subscriptions) must be torn down in
  `onWillUnmount` — OWL does not auto-pair these.
- Custom field widgets are registered via `registry.category("fields").add(...)`, not by
  subclassing `AbstractField`.

### Baseline environment (Odoo 19)
- PostgreSQL 17 and Python 3.12 are required. Code assuming an older PostgreSQL/Python baseline
  (e.g. certain SQL feature checks) should be re-verified against 19's baseline before being
  presented as compatible.

## Practical rule for the agent

Before writing or approving a code diff, state (even just internally in reasoning) which Odoo
version it targets and why. If the conversation never confirmed it, ask — a one-line clarifying
question is cheaper than generating code with `attrs=` or `name_get()` for an instance that will
reject or silently mis-render it.
