# Legal and Asset Hygiene

This repository is an independent community project for local Home Assistant
use with compatible MENNEKES AMTRON wallboxes.

## License

The project code and project documentation are released under the Apache
License, Version 2.0. See [../LICENSE](../LICENSE) and [../NOTICE](../NOTICE).

The Apache License, Version 2.0 applies only to files that are part of this
repository. It does not grant rights to MENNEKES firmware, documentation,
applications, protocols or trademarks, or to third-party projects.

## Trademark notice

MENNEKES, AMTRON, Home Assistant, HACS, Nabu Casa, Anthropic, Claude and other
referenced names are trademarks or names of their respective owners. They are
used only for compatibility, attribution, and descriptive reference. Modbus is
a registered trademark of Schneider Electric USA, Inc., licensed to the Modbus
Organization, Inc.

This project is not affiliated with, endorsed by, sponsored by, or certified
by MENNEKES Elektrotechnik GmbH & Co. KG, Home Assistant, Nabu Casa, HACS, or
the referenced community projects.

## No vendored vendor documentation

The manufacturer's Modbus RTU specification and installation manual are
**referenced, never vendored**. The repository must not contain:

- MENNEKES PDF documents or extracted copies of them,
- MENNEKES configuration-tool payloads,
- product photography or marketing material.

`docs/modbus-registers.md` is the project's own description of the register
map. It restates register addresses, data types, numeric ranges and
enumerated values, which are facts about a protocol and are what a reviewer
needs in order to check the code. The specification's explanatory prose is not
reproduced; it is rewritten.

The document is identified by publisher, title, revision and date so that any
reader can obtain it from MENNEKES directly.

## No firmware or configuration-tool payloads

The repository must not contain:

- MENNEKES firmware images or extracted firmware trees,
- configuration-tool binaries or their payloads,
- local device backups,
- runtime artifacts, network traces, logs with private data, or local secrets,
- serial-port paths that identify a specific USB adapter.

`scripts/check_repo.py` and `scripts/check_legal_audit.py` enforce this, and
`.gitignore` excludes `*.pdf` outright.

## Project-owned generic artwork

The integration brand images under
`custom_components/mennekes_amtron/brand/` are project-owned generic artwork.
They use an abstract plug-and-bolt motif to describe the integration concept.
They are not MENNEKES, Home Assistant, HACS or Nabu Casa logos, and they are
not copied or derived from firmware, product photography or marketing
material.

`icon.png` and `logo.png` contain the same 256x256 PNG artwork. Their SHA-256
hash is:

```text
1ec09c77821817d235eb180da6ca006ab305074ffdcda0e2a44b7b85a4897c63
```

The legal gate fails if either hash changes without review.

## Runtime dependencies

`manifest.json` requests exactly one runtime dependency, `pymodbus`, and
declares it as a minimum rather than an exact pin so Home Assistant keeps
owning the resolved version. `scripts/check_pymodbus_pin.py` enforces that.
pymodbus is published on PyPI under the BSD-3-Clause license; it is not
vendored into this repository.

## Repository gate

`scripts/check_repo.py` is the local and CI gate for repository hygiene, and
`scripts/check_legal_audit.py` for this policy. Together they reject forbidden
payload suffixes, foreign runtime directories, private keys, identifying
device paths, unexpected runtime requirements and brand-asset changes.

The current local provenance evidence is retained under
[docs/audits/current-legal-provenance.md](audits/current-legal-provenance.md).
That file is a technical audit snapshot, not a live legal opinion.
