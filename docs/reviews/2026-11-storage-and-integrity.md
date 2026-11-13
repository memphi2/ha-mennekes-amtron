# Audit: storage and data integrity (0.9.0b2)

Date: 2026-11-13. Scope: everything the Modbus service app and the integration
save: control intent, charging job, weekly plans, departure plan with what it
learned, charge rates, the service token, and Home Assistant's own stores.
Method: reading the code, plus probes against the real stores and controllers
with about 3,300 damaged files (empty, truncated, foreign identity, other
version, NaN, oversized numbers, deep nesting, wrong types), failing and
interrupted writes and long runs; no hardware.

The questions for every file: does a damaged, foreign or unwritable file stop
the app, grant charging it should not, lose a pause that nothing will end, or
wear the disk?

## Files examined

| File | Write | Damaged or foreign file | Writes | Result |
|---|---|---|---|---|
| `intent.json` (pause, release, resume current, targets, last session) | temp file, fsync, rename, directory fsync, owner only | kept the app from starting, for good | on change | fixed (1, 5) |
| `charge-job.json` | same, at most 8 KiB | starts are fenced, pauses run | on decision change | ok, (6) |
| `weekly-plans.json` | same, at most 128 KiB | weekly plans are off, the app runs | on edit or dispatch | fixed (3), ledger fixed (8) |
| `departure-plan.json` with the learned cars | same, at most 8 KiB | the plan is cleared, Home Assistant hands the trip over again | on decision change | fixed (2, 4, 6, 7) |
| `charge-rates.json` | same, at most 64 KiB | empty history, warning | at most every 10 min | ok |
| `service-token` | same, owner only | configuration error | once | ok |
| Home Assistant's stores under `.storage` | Home Assistant's atomic writer, at most 32 KiB on load | targets and trip: entry not ready; the rest: ignored with a warning | on change | ok |

No damaged file was accepted in a way that permits charging; NaN and infinity
never pass. Saves are serialised per store and shielded against cancellation,
and a failed directory sync forces the next write. No VIN and no token is
saved in plain text.

## Findings and what changed

1. **A rejected `intent.json` kept the app down.** A file of another wallbox
   (after a replacement or a restored backup), of another format, or a
   damaged one stopped the start on every attempt: no heartbeat, no control,
   and the app user cannot reach `/data`. Such a file now counts as none: the
   app starts as on its first run, takes the wallbox's own state, logs a
   warning once and replaces the file with its first save.
2. **A lost plan save could orphan the plan's pause.** The pause reached the
   wallbox before the plan had saved that it owned it. After a restart in
   between, nothing ended that pause when Home Assistant withdrew the trip.
   The plan now saves its pause first; a saved pause that never applied ends
   harmlessly.
3. **One failed save switched the weekly plans off until the app restarted.**
   Every edit, activation and stop was refused on a healthy disk. Without an
   active plan the file is now read again every ten seconds; once it reads
   back, the plans work again. A car whose stop by hand could not be saved is
   forgotten first, before any plan is served.
4. **Charging to the minimum level rewrote the plan file on every meter
   reading**, about 120 times an hour, because the still-future start moves
   with every reading. Only a start that passed is saved now; one ahead is
   computed again after a restart.
5. **A failing disk at start kept the app down.** The first save of the
   control intent is now handled like every later one: logged, and the
   wallbox stays in use.
6. **An oversized number or an extreme time offset in a saved file ended the
   process at every start.** These count as a damaged file now, and an
   extreme offset in a command is an invalid time.
7. **The learned cars lost their order at a restart.** The car unused for
   longest makes room for a ninth one; after a restart the alphabetically
   first one did. The cars are saved as a list in their order of use.
8. **The ledger of used weekly dates never made room.** Once 128 plans had
   ever used a date, every new plan failed to start its first order. A full
   ledger now drops the oldest date of a deleted plan; the existing plans keep
   theirs.

## Residual risks, not verified

- A rejected `intent.json` loses the saved targets and pause intent of that
  file. The wallbox's own registers keep a pause the app wrote; a target has
  to be set again.
- A damaged `weekly-plans.json` keeps the weekly plans off until the file is
  readable; there is no reset in the app.
- A date is marked as used before its order is created. A kill between the
  two skips that departure, by design: a date never runs twice.
- Temporary files left by a kill between creating and renaming them are not
  cleaned up; each is at most the size of its file.
- A damaged energy or percentage target in Home Assistant's own store needs
  the file to be removed by hand.
- Restore data of entities, option writes of Plug & Charge and vehicle
  detection, and the Supervisor options transaction were only skimmed.
- Durability relies on `/data` honouring fsync.
