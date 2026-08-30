# Security Policy

## Supported versions

Security fixes are considered for the latest published release and the current
`main` branch.

## Reporting a vulnerability

Please do not open a public issue for a vulnerability that could expose
credentials, data, services, or users.

Use GitHub's private vulnerability-reporting mechanism:

https://github.com/aariiparekh3012-collab/quantproject2/security/advisories/new

If private vulnerability reporting is unavailable, open a minimal public issue
requesting a private contact channel. Do not include exploit details, secrets,
personal information, or sensitive logs in that issue.

A useful report includes:

- the affected component and version;
- steps to reproduce;
- the likely impact;
- a minimal proof of concept, if safe;
- suggested remediation, if known; and
- whether the issue is already public.

Reports are handled on a best-effort basis. Please allow the maintainer a
reasonable opportunity to investigate and prepare a fix before public
disclosure.

## Credentials and market data

Never commit brokerage credentials, API tokens, account identifiers, personal
data, or redistribution-restricted market data. Use environment variables and
the provided `.env.example` pattern.

