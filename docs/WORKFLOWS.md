# Ecommerce workflows

Workflow definitions live in `config/workflows.json`. The included workflows navigate to an authorised service and stop at a human checkpoint. They do not publish or spend.

## Supported step types

- `navigate`: opens an allowlisted URL with Playwright.
- `checkpoint`: records a human-review checkpoint.
- `publish`: always creates an approval request.
- `spend`: always creates an approval request and carries an AUD amount.

External mutation is intentionally adapter-based. A `publish` or `spend` approval currently records the decision but does not mutate a service until a site-specific adapter is implemented and tested.

## Adding a workflow

1. Add a unique workflow ID and short description.
2. Keep the declared `costAud` at zero unless it truly incurs a charge.
3. Add only origins already present in `JARVIS_ALLOWED_ORIGINS`.
4. Put every irreversible or paid step behind `publish` or `spend`.
5. Test against a sandbox account before production use.

Keep credentials in the service's own browser session or an OS-backed secret store, never in this repository.
