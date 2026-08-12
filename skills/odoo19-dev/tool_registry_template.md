# MCP Tool Registration Template

Copy this block per tool when adding a new capability to the agent's tool registry. Every field is
required — an incomplete entry is a policy gap, not an optional detail to fill in later.

```yaml
tool_name: prepare_sale_order
description: >
  Builds a draft sale order preview (customer, lines, pricing, tax) from validated inputs.
  Does NOT create the order — see create_sale_order for that.
input_schema:
  customer_id: {type: integer, required: true}
  lines:
    type: array
    items:
      product_id: {type: integer, required: true}
      qty: {type: number, required: true}
allowed_model: sale.order
allowed_operation: read           # read | create | write | unlink
required_permission: base.group_sale_salesman
risk_class: C                     # A|B|C|D|E — see SKILL.md §3
confirmation_requirement: preview # none | preview | explicit_approval
validation_rule: >
  Reject if customer_id not found, product not sellable, qty <= 0,
  or price cannot be resolved from an active pricelist.
audit_policy: >
  Log requester, input payload, resolved preview, timestamp. No PII beyond
  what's already visible to the requesting user's ERP role.
rollback_possible: n/a (no record created by this tool)
```

## Rules for filling this in

- **input_schema** must be a strict typed schema. Never accept a free-text ORM domain, raw SQL
  fragment, or "any JSON the model wants to send" — that's the exact gap that lets an LLM
  hallucinate a plausible-looking-but-wrong query instead of using verified structured input.
- **risk_class** determines `confirmation_requirement` by default (A→none, B→none/logged,
  C→preview, D→explicit_approval, E→not exposed as a direct tool at all — route to the
  human-review pipeline instead).
- **required_permission** must map to a real Odoo group/ACL — never a permission broader than
  what the requesting user already holds (see the `AI Permission = User ∩ Policy ∩ Tool ∩ Risk`
  rule in SKILL.md §3).
- A tool that only *previews* (e.g. `prepare_sale_order`) should be `allowed_operation: read`
  even though it's building something that looks like a write — keep prepare/create as two
  separate tools so the confirmation step has a real gap to sit in.
- If a capability doesn't cleanly fit an existing tool, write a new registry entry rather than
  broadening an existing tool's schema to "also handle" an unrelated case — narrow, single-purpose
  tools are what make the model's tool choice (and therefore its grounding) auditable.
