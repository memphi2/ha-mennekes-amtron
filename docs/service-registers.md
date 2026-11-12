# Register explorer

`Registers` is a passive, administrator-only Ingress page. It lists every
register from the maintained register map, not a scan of undocumented addresses.
Raw 16-bit words (hexadecimal and decimal), decoded values, enum labels,
last successful update, last attempt, and observation quality are separate.

- **OK**: a successful usable read within its cadence. A nonzero wallbox error
  register can have OK observation quality while describing a device fault.
- **Degraded**: a previous valid value survives a failed/overdue update, or an
  unknown enum value was returned. Its original timestamp remains visible.
- **Error**: no usable sample or no current device connection / expired grace.
- **Unknown / unsupported / write-only** are explicit, not painted green.

One-time identity registers do not become stale merely because they are not
periodically polled. Active/idle and configured polling cadences are respected.
Acknowledged writes never substitute for readback. Serial-number words are
redacted in memory and in responses; authentication packets are not observed.

Search, filtering, sorting and freezing affect the browser only. Opening or
refreshing the page generates no extra Modbus traffic or Recorder writes.
Cached browser data is hidden when requests fail; background tabs stop polling.
