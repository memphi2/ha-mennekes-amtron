# Blueprints

The integration ships four automation blueprints. Setting it up installs them
into `config/blueprints/automation/mennekes_amtron/`, and they appear under
*Settings → Automations & scenes → Blueprints*.

| Blueprint | What it does |
|---|---|
| **Charge from PV surplus** | Follows a surplus sensor with the charging current, divided by the phases the wallbox will really use and clamped to the range it accepts |
| **Pause and resume on PV surplus** | Pauses when the surplus stays low and resumes when it stays high, with two thresholds and a hold time |
| **Recover from a lost heartbeat** | Presses the recovery button when the wallbox reports that the energy manager is unavailable |
| **Notify on a grid-operator downgrade** | Sends a notification when a §14a downgrade starts and when it ends |

## Using one

*Settings → Automations & scenes → Blueprints → Create automation*, pick the
blueprint, fill in the entities. Every entity picker is filtered to this
integration, so you only see your wallbox's entities — except the PV surplus
sensor, which comes from wherever your inverter lives.

## Why they are split the way they are

Following the surplus and pausing on it look like one job and are not. The
current can be adjusted every few seconds; a pause has to wait, because the
manufacturer asks for hysteresis and more than five minutes between a pause
and a resume. Putting both in one automation means one of them gets the wrong
timing. So one blueprint writes the current, the other decides whether
charging happens at all, and they are independent.

The pause blueprint uses the **charging-pause** switch, which signals 0 A to
the vehicle. It deliberately does not use the charging release, because that
switches the wallbox relay and wears it out.

## Your edits are yours

The installer records a digest of every file it writes. On the next start it
replaces a file only if it still matches what was written; a blueprint you
edited is left alone, and so is a file you created under the same name. A
blueprint that is withdrawn in a later version is removed only if it was never
touched.

If you want to start from a shipped blueprint and change it, copy it to a new
filename first. Then updates to the original never fight your copy.

## Writing your own instead

[automations.md](automations.md) has the same logic as plain YAML
automations, with more explanation and more room to adapt. The blueprints are
the packaged form of those recipes, not a different mechanism.
