# Current Legal and Provenance Audit Snapshot

Audit date: 2026-10-04

This is a technical repository provenance and release-readiness audit. It is
not a legal opinion and does not replace review by qualified counsel.

## Audit base

- Release line: `0.1`
- Scope: the initial repository content, created in one local working session.
- The repository had no remote at audit time, and nothing had been published.

## Scope

In scope:

- Tracked repository source content.
- Legal, security and privacy documentation.
- Runtime package metadata and the single runtime dependency.
- Development and validation lock files.
- Brand-asset provenance and trademark-notice hygiene.

Out of scope:

- Legal advice.
- Jurisdiction-specific patent or trademark clearance.
- **Public clone and similarity search.** The reference repository's audit
  runs GitHub Code Search fingerprint queries at this point. This project was
  built under an explicit local-only instruction, so no external search was
  performed. It is also not meaningful yet: no line of this repository has
  ever been published, so there is nothing for a public index to contain.
  Run the fingerprint search before the first public release.

## Method

- `git ls-files` inventory.
- `scripts/check_repo.py`: required paths, tracked runtime artifacts, JSON
  validity, release metadata, HACS metadata, requirement pins, CI pinning,
  secret and identifying-path patterns, Python compilation.
- `scripts/check_legal_audit.py`: forbidden payload suffixes and directories,
  content-based detection of a tracked copy of the vendor specification,
  required legal documents, required wording in `README.md`, `NOTICE` and
  `docs/legal.md`, bounded runtime requirements, brand-asset hashes.
- A verbatim-overlap measurement between the specification's text and every
  tracked document and source file, reported under "Verbatim overlap" below.
- `scripts/check_register_map.py`: register map against entities,
  translations and icons.
- `scripts/check_pymodbus_pin.py`: manifest pin against Home Assistant Core's
  own pin.
- `sha256sum` of the brand assets.

## Source provenance

The device contract comes from one document: MENNEKES, "Modbus RTU
Specification -- AMTRON 4You 300 / Compact 2.0s / Start 2.0s", document
revision 2.5, dated 2025-07-11, internal register layout v01.03.

The document is **not** vendored. `docs/modbus-registers.md` restates register
addresses, data types, numeric ranges and enumerated values -- facts about a
protocol, and the minimum a reviewer needs to check the code -- in the
project's own wording. The specification's explanatory prose is not
reproduced.

The pymodbus API used in `client.py`, `_client_read.py`, `_client_write.py`
and `decode.py` was verified against the pymodbus 3.11.2 and 3.13.1 sources --
the versions Home Assistant Core resolves for the minimum and the current
validation matrix -- not from memory: the keyword-only `count=` and `device_id=` parameters, the
`FramerType.RTU` framer and the `convert_from_registers` /
`convert_to_registers` signatures.

## Verbatim overlap with the specification

Every tracked Markdown, Python, JSON and YAML file was compared against the
text of the specification for shared word sequences of seven words or more,
after normalising case and punctuation. The result is that nothing of the
document's prose was reproduced:

- The longest shared sequences, at 19 and 11 words, are lists of **register
  names** in `registers.py` and `register_blocks.py` -- `current_l1`,
  `voltage_l1`, `power_l1` and so on. They are identifiers of an interface,
  not expression, and an implementation cannot differ from them and still
  interoperate.
- In `docs/modbus-registers.md` the longest matches are eight and nine words
  and are all **enumerated values with their short labels**, for example
  `5 charging 6 error 7 service mode`. They are the facts an implementer
  needs, restated in a table of the project's own design.
- The remaining matches are **numbers and hardware references**: the list of
  supported baud rates, `8 data bits 2 stop bits no parity`, `bank S1 DIP 4
  and DIP 5`, and the statement that addresses from 10 to 50 can be set.
- `docs/safety.md` contains one nine-word sequence of the document's prose,
  `are automatically limited by the configuration of the wallbox`, as an
  attributed quotation introduced with "The specification states that". It is
  a short, purposeful and sourced citation.

No sentence, paragraph or explanatory passage of the specification appears in
this repository.

## Result

- No forbidden payload suffix, archive, firmware image, PDF or capture is
  tracked. `.gitignore` excludes `*.pdf` outright.
- No foreign runtime directory (`vendor`, `third_party`, `external`, `dist`,
  `node_modules`, `firmware`, `extracted`) is tracked.
- No private key, SSH key or identifying `/dev/serial/by-id/usb-...` path is
  tracked.
- `LICENSE` is the unmodified Apache License, Version 2.0. `NOTICE` carries
  the project notice, the non-affiliation statement and the trademark notice.
- `manifest.json` requests exactly one runtime requirement,
  `pymodbus[serial]>=3.11.2`. It is a minimum, not a pin: Home Assistant Core
  pins pymodbus for the whole instance and moves that pin between releases, so
  an exact pin here would conflict with Core. `codeowners` is empty, so no
  personal metadata ships in the package.
- `README.md` marks the project as unofficial in its title and repeats the
  non-affiliation statement.

## Brand assets

```text
1ec09c77821817d235eb180da6ca006ab305074ffdcda0e2a44b7b85a4897c63  custom_components/mennekes_amtron/brand/icon.png
1ec09c77821817d235eb180da6ca006ab305074ffdcda0e2a44b7b85a4897c63  custom_components/mennekes_amtron/brand/logo.png
```

Both files are the same project-owned 256x256 PNG, generated in this
repository from a plain geometric description: a rounded square, four circular
marks and a lightning-bolt polygon. No vendor logo, product photograph or
third-party artwork was used as a source. `docs/legal.md` records the hash and
the legal gate fails if it changes.

## Trademark and naming review

The repository uses the MENNEKES, AMTRON, Home Assistant, HACS and Modbus
names descriptively, for compatibility and user orientation. `README.md`,
`NOTICE` and `docs/legal.md` mark the project as unofficial and not
affiliated. No vendor logo, product-image crop or certification badge is
tracked, and the README uses a generic HACS custom badge rather than the
"Works with Home Assistant" badge.

This is clean for repository hygiene. It is not a trademark clearance
opinion. Keep the compatibility wording descriptive, keep the
unofficial and non-affiliation notices visible, and do not add vendor logos or
wording that implies official status without separate legal review.

## Open limits

- No public clone or similarity search was performed; see Scope.
- No jurisdiction-specific patent or trademark clearance was performed.
- No legal opinion was issued.
- The device behaviour described in the documentation is taken from the
  manufacturer's specification and has not yet been measured on hardware. The
  verification plan in `docs/quickstart.md` and `docs/safety.md` is the step
  that closes that gap.
