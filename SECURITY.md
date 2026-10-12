# Security

InboxZeroTokens is a local IMAP watcher. It is designed to meet the applicable parts of [OWASP ASVS 5.0 Level 2](https://owasp.org/www-project-application-security-verification-standard/) for a CLI that handles mailbox credentials and untrusted email headers.

## What this tool does not do

- It does not run an LLM.
- It does not mark mail as read (`EXAMINE` + `BODY.PEEK` only).
- It does not put passwords, tokens, or HMAC keys in the config file.
- It does not log message bodies, passwords, or authorization headers.

## Secrets (ASVS V6)

- Config may only *reference* secrets: `env:`, a `0600` file, or the OS keychain via the optional `keyring` extra.
- Plaintext `password = "..."` in the config is rejected.
- Secret files that are group- or world-readable are refused on POSIX.
- State and drop files are written `0600` via an atomic replace.
- `izt doctor` reports whether a secret can be resolved; it never prints the secret.

## Network (ASVS V9 / V12)

- IMAP uses `IMAP4_SSL` with `ssl.create_default_context()` — certificate verification is always on.
- Webhooks use the same TLS context. Destinations must be `https` except `http://localhost`, `127.0.0.1`, or `::1`. There is no `--insecure` flag.
- Redirects are not followed. Any 3xx response is a failed wake; state does not advance. Authorization headers are never replayed to another origin.
- HMAC (when configured) is `HMAC-SHA256(secret, timestamp + "." + body)` with `X-InboxZeroTokens-Timestamp`. Receivers must verify the signature and reject `|now - ts| > 300` seconds to limit replay.
- Logins are staggered and capped per pass so a bad password does not hammer a provider.

## Untrusted mail (ASVS V5 / V1)

- Headers are treated as hostile: control characters are stripped, lengths are capped, and addresses are normalized before matching.
- The command wake target takes a fixed argv list from config. Mail fields are written to **stdin as JSON** and are never interpolated into a shell command.
- Wake payloads include a `note` that items are untrusted input.
- Message bodies are omitted unless `settings.include_bodies` is explicitly true.

## Logging (ASVS V7)

- Logs go to a local file you configure.
- `SafeLogger.log(..., fields=)` calls `refuse_secrets` and refuses mappings whose keys look like a password, token, or key.
- Failed webhook errors log the exception *type* or HTTP status, not the URL with credentials.

## Dependency hygiene (ASVS V3)

- Runtime: Python 3.9+ standard library only.
- Optional: `keyring`.
- Dev extras are pinned (`pytest`, `ruff`).
- There is no CI yet. Run `pytest` and `ruff check .` locally before contributing. Maintainer-private deny tokens are **not** stored in the repository; load them only via gitignored `IZT_PRIVATE_PATTERNS_FILE` or `IZT_PRIVATE_HASHES`.

## Reporting

Open a private security advisory on the GitHub repository if you find a vulnerability. Please do not file a public issue for credential-handling bugs.
