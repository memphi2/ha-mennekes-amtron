# Runtime settings and change preview

The administrator-only **Runtime** page compares effective values, saved
Supervisor values and the proposed edit. It requires an explicit preview and
confirmation. All writes reuse the existing Supervisor transaction, revision,
CSRF checks and uncertain-write fence; there is no second configuration store.

Status and measurement polling intervals can be applied without disconnecting
the adapter or restarting the heartbeat. Existing block freshness, retry history,
queued control commands and phase transitions are retained. An in-flight poll
or existing sleep finishes normally, then the new cadence applies. Saving is
not a force-refresh operation.

Service current cap, state-change interval and phase-switch grace remain
**restart-required** in this step. A saved lower current cap is NOT immediately
effective. The old effective cap is shown until restart. Neither this page nor
its API performs an automatic restart or modifies charging release. Connection,
read-only and listen/port changes saved elsewhere are also flagged as pending
restart without exposing addresses, credentials or the service token.

Preview is read-only. A stale revision or detected concurrent native Supervisor
edit is rejected. A lost save reply is confirmed by exact readback or fences
further saves. If options were saved but hot application could not complete,
the response says `runtime_apply_pending`; reload shows the distinct saved and
effective values. No POST is automatically repeated by the browser.

Tests cover validation boundaries, authorization, confirmation, stale revisions,
concurrent saves, lost replies, preserved bus state and offline UI interactions.
No hardware or live Supervisor/browser-session validation is claimed.
