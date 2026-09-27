# Security

## Reporting

Do not post credentials or exploitable details in a public issue. Once this
repository is public, use GitHub's **Report a vulnerability** feature if the
maintainer has enabled private vulnerability reporting. Otherwise contact the
maintainer privately through a contact route on their GitHub profile and ask
for a private reporting channel before sharing details.

Never include real Wi-Fi configuration, a saved pairing identity/key file,
personal photos, or an unredacted scanner report. There is no guaranteed
response time or formal supported-release policy yet.

## Threat model

Work Status is a trusted-LAN hobby application, not an Internet-facing service.
It uses physical code comparison, X25519 pairing and HMAC-SHA256 request/response
authentication. One-time challenges expire and cannot be replayed. Uploads also
validate ownership, offsets and a complete-file SHA-256 checksum.

**HTTP is not encrypted.** Authentication does not hide pictures, notes or
device identifiers. Use WPA2/WPA3, avoid untrusted/shared networks, and do not
forward port 8080. Pairing/bootstrap endpoints are unauthenticated and the
embedded server has limited resources; it is not hardened against network
denial of service. This is custom protocol code, not an independently audited
cryptographic system.

The laptop's per-badge keys are stored in a local JSON file, not an OS credential
vault. A person who copies that file can act as the paired controller. Protect
the local account and backups. Clearing trust on the badge revokes every
remembered controller. Forgetting a desktop profile does **not** revoke trust.

## Data handling

Work Status media processing occurs on the laptop. Only converted frames are
sent to the selected badge. Media is saved on writable badge storage and can
remain there after leaving photo mode. The dependency installer contacts Python
package repositories; video decoding uses the ImageIO FFmpeg backend.

The other apps have separate network behaviour: Desk Clock uses Internet
location/time services, and Currency Board requests public exchange rates.
They do not share Work Status pairing keys.

## Publication checks

[tools/audit_secrets.py](tools/audit_secrets.py) and
[tools/gitleaks.toml](tools/gitleaks.toml) scan publishable current files,
reachable Git history, historical blob contents, commit messages and Wi-Fi
assignments. CI repeats the audit with a full checkout.

A clean report means **no matches were detected within that scope**, not proof
that no secret exists. Image pixels are not OCR-scanned. Unreachable Git
objects, deleted remote refs, GitHub artifacts/releases, forks and other clones
are outside the audit. Previously public data may remain cached elsewhere.

If a secret is found, rotate it first. Removing it from the working tree or
adding an ignore rule is not enough to remove it from history. History rewriting
and force-pushing must be coordinated and explicitly approved.
