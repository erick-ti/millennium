# Evaluated alternatives

This directory holds infrastructure that was evaluated and prepared but is not
the live deployment. It is kept as a record of the options considered.

## Railway (managed PaaS)

Before the self-hosted VPS deployment, Railway was evaluated as a managed
platform-as-a-service target and fully repo-prepped: per-service
config-as-code (`railway/*.railway.json` for the backend, the frontend, and the
four scheduled jobs) plus a step-by-step runbook
(`railway-deploy-runbook.md`). The project shipped to a self-hosted VPS
instead. See `infra/hetzner/` and the deploy section of
[ARCHITECTURE.md](../../ARCHITECTURE.md) for the live setup. These files are
retained for reference; the runbook's environment matrix predates a later change
that dropped Redis in production.
