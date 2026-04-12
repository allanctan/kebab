# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in KEBAB, please report it privately:

**Email:** allanctan@gmail.com

Do not open a public GitHub issue for security vulnerabilities.

## Scope

KEBAB is a CLI tool that processes documents locally. Security concerns include:

- Secret leakage (API keys, credentials in logs or committed files)
- Path traversal in file I/O operations
- Prompt injection via source documents that could affect LLM behavior
- Unsafe deserialization of frontmatter or pipeline state files

## Response

I will acknowledge reports within 48 hours and provide a fix timeline within 7 days.
