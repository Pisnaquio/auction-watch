# Security policy

## Reporting a vulnerability

Please report suspected vulnerabilities privately to the repository
maintainers before opening a public issue. Include a concise description, the
affected version, reproduction steps, and impact. Do not include credentials,
personal data, or live service details in a report.

## Project rules

- Never commit secret values, private keys, credentials, or personal data.
- Runtime configuration is supplied through the environment or the deployment
  platform's secret store.
- The public example configuration contains fictitious, non-sensitive values.
- Profiles are user configuration, not a place for credentials or recipients.
- Write endpoints will receive authentication and authorization before public
  network exposure is supported.
- Future webhook delivery must validate destinations and prevent SSRF.
- Logs and exported artifacts must be safe to share and must not contain secret
  values.
