# Security policy

## Reporting

Report vulnerabilities privately through GitHub Security Advisories on this
repository. Do not open a public issue.

Include what you found, how to reproduce it, and what an attacker gains. You will get
an acknowledgement within seven days.

## Scope

In scope: authentication and session handling, the OIDC implementation, API key
handling, path traversal in media and import paths, SSRF through indexer or download
client URLs, credential storage, privilege escalation between user and admin roles,
and injection anywhere.

Out of scope: anything requiring an already-compromised host, denial of service by
resource exhaustion on a self-hosted instance, and vulnerabilities in the indexers or
download clients Pornarr talks to.

## Supported versions

Pre-1.0, only the latest release is supported. After 1.0 this section states a policy.
