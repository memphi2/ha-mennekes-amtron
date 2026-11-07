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

MENNEKES, AMTRON, Home Assistant, HACS, Nabu Casa, Modbus and other
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

`scripts/check_legal_audit.py` enforces this by content, not only by file
extension: a tracked text file that carries two or more structural phrases of
the specification is rejected, because a text extraction of the document is a
copy of it whatever the file is called. A citation of its title carries none
of those phrases and passes.

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
`custom_components/mennekes_amtron_rtu/brand/` are project-owned generic artwork.
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

`manifest.json` requests `pymodbus[serial]>=3.13.1`; Home Assistant owns the
resolved version. `scripts/check_pymodbus_pin.py` checks compatibility with
HA's pymodbus version. pymodbus is published under the BSD-3-Clause license.
The optional LAN-adapter login uses PyCA cryptography, which Home Assistant
provides as one of its own dependencies, under
[Apache-2.0 OR BSD-3-Clause](https://github.com/pyca/cryptography/blob/main/LICENSE).
Neither dependency is vendored into this repository.

The HACS release SBOM lists the integration and its manifest-declared runtime
requirements; it does not enumerate Home Assistant's own dependency tree.
The standalone service explicitly declares cryptography and `aiohttp`, which
Home Assistant also provides for the integration client. Python packages and
their license notices are installed from their upstream distributions. The
container build uses `requirements-service.txt`; the shared project-owned driver
is not a third-party vendored payload.

## Optional Waveshare adapter support

The adapter implementation is project-written interoperability code. It uses
protocol field definitions and independently checked behavior; no vendor
application, SDK source, firmware image, document extraction or captured device
configuration is distributed in this repository. Tests construct synthetic
parameter images, identities and credentials rather than retaining device data.

Sources are the parameter-management documentation and tools linked from the
[Waveshare single-channel adapter wiki](https://www.waveshare.com/wiki/RS485_TO_POE_ETH_(B)),
plus the manufacturer-supplied
[TCP Secure Handshake Guide](https://drive.google.com/file/d/1NRPLhAJzy-iUi_50va8iRjyM60u2iHa5/view)
and its [reference script](https://drive.google.com/file/d/1Gh0A4JFFqFNti8vNKh5M1ddvdUcN4CpZ/view).
Those materials were used as protocol references, not copied into the runtime
implementation. The project license does not grant rights to these external
materials or Waveshare trademarks.

Support is constrained to the documented family and verified firmware/layout;
see [supported LAN adapters and verification limits](lan-adapters.md).
The TCP handshake has a confirmed firmware weakness and does not encrypt
Modbus traffic. Compatibility support is not a security certification.

## Repository gate

`scripts/check_repo.py` is the local and CI gate for repository hygiene, and
`scripts/check_legal_audit.py` for this policy. Together they reject forbidden
payload suffixes, foreign runtime directories, private keys, identifying
device paths, unexpected runtime requirements and brand-asset changes.

The current local provenance evidence is retained under
[docs/audits/current-legal-provenance.md](audits/current-legal-provenance.md).
That file is a technical audit snapshot, not a live legal opinion.

## Optional tariff data

Imported data comes from [Par14a-Database (the78mole)](https://github.com/the78mole/Par14a-Database)
under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Operator matching
uses VNBdigital, operated by BDEW. Original operator price sheets remain
authoritative. The repository does not distribute their PDFs.
