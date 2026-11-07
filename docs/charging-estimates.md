# Charging losses and costs

These optional features are available in **stable 0.6.1**, including automatic
tariff import and yearly refresh. For service connections,
[update the app first](installation.md#updating-to-061).

Both features are optional. Open **Settings → Devices & services → Mennekes
Amtron RTU → Configure** and choose the sources you want. They work with a
direct USB/LAN connection and with the Modbus app/service. No extra Modbus
polling, vehicle commands or electricity-provider login is needed.

The wallbox must provide its lifetime energy counter (register layout v01.02
or newer). In service mode, the service must support charging summaries and,
for costs, timestamped meter observations. An older compatible service still
supports its existing functions, but cannot supply these estimates.

A completed charge here means **one connection cycle, ending when you unplug**.
Pauses remain part of that cycle. Observe the wallbox while unplugged before
starting the first charge. Starting observation halfway through charging,
missing connection transitions or an unreliable counter cannot produce a
complete estimate. The last result becomes unknown when the newest completed
cycle cannot be matched; a previous result is not passed off as the new one.

## Charging losses: choose the available data

| Source | What you select | How the charge is matched |
|---|---|---|
| Disabled | Nothing | No charging-loss entities or vehicle subscriptions. |
| BavarianData charging history | The vehicle in the device picker | Complete local charging history with matching start/end times. |
| Vehicle energy sensor | An existing sensor for battery energy from the last charge | Explicit assignment, or an optional new-value heuristic. |

The two sensors are **Estimated charging loss, last charge** and **Average
estimated charging loss**. The average weights each accepted charge by energy:
`(sum of wallbox kWh − sum of battery kWh) / sum of wallbox kWh × 100`.
For example, 10 kWh at the wallbox and 9 kWh reaching the battery gives 10%.
Correcting the latest assignment replaces that sample instead of adding it twice.
Changing the selected vehicle or source starts a new average.

The vehicle value must describe **energy entering the battery**. A vehicle's
AC-input or grid-energy value is a different measurement boundary and does not
establish these losses. Unknown, zero, negative or greater-than-wallbox values
are not converted into a misleading zero-loss result. Neither loss sensor
creates long-term statistics.

### BavarianData

Select **BavarianData charging history**, then the vehicle. The integration reads
BavarianData's existing local history through its public
[`get_charging_sessions` action](https://justchr.github.io/BavarianData/docs/services/).
It never requests a BMW history refresh or spends the BMW API quota.

Only complete, non-overlapping records whose charging boundaries fit the
observed wallbox cycle are accepted. Separate records caused by a pause may be
combined. DC charges and open, interrupted, late-start, ambiguous or truncated
histories remain unmatched. The last loss stays unknown until a suitable record appears; local
reads are retried a limited number of times and on charging-stop events.

BavarianData's `energy_kwh` is estimated battery energy integrated from vehicle
telemetry. Its `grid_kwh` is grid energy and is deliberately not substituted.
A BMW-imported session that only has grid energy cannot supply this comparison.
The result therefore remains an **estimate**, even with a good time match.
See [BavarianData charging history](https://justchr.github.io/BavarianData/docs/charging-history-and-cost/).

### Only a “last charge kWh” sensor

Select **Vehicle energy sensor** and choose the existing entity. It must report
`kWh` or `Wh`. After unplugging, wait for the vehicle integration to update, then
press **Assign vehicle charge energy**. Check that the value belongs to this
charge before assigning it. This button only updates the estimate and also
works when wallbox controls are read-only.

**Assign changed energy automatically** is optional and off by default.
It accepts a valid number changing to a different valid number within 30 minutes
after an observed completion, while the wallbox is unplugged. Startup values,
restored values and an unknown value becoming available do not qualify.

A changed value does not prove which charge it belongs to. Use manual assignment
if the vehicle has charged elsewhere, updates arrive late, or two consecutive
charges have the same kWh value. Without charging timestamps the result is always
an estimate. This setting does not automatically identify which vehicle is plugged
in; optional multiple-vehicle profiles are not part of this implementation.

## Charging costs: fixed or dynamic price

Under **Charging cost calculation**, choose **Fixed electricity price** or
**Electricity price sensor**. For a fixed rate enter your gross
price in **EUR/kWh**, for example `0.30` for 30 cents. With Tibber or another
provider, select its existing HA sensor for the **current electricity price**.
Supported units are `EUR/kWh`, `€/kWh`, `ct/kWh`, `EUR/MWh` and `€/MWh`.
Negative prices are supported. Other currencies or a missing unit are not guessed.

A dynamic price stays valid for **90 minutes after its last report to Home
Assistant**. Reporting the same value again renews that period. If reports stop,
energy after expiry remains unpriced until a valid report returns; the old
price is not carried forward indefinitely. No extra provider request is made.
This expiry does not apply to a configured fixed price.

If the selected value already contains the complete price you actually pay,
leave **Network price adjustment** at **None; source is the final price**. This
also applies when your provider has already included your reduced or time-variable
network charges. Do not apply the same adjustment twice.

**Estimated charging cost, current charge** shows the running estimate and is
**disabled by default** to reduce Recorder writes. Enable it on the wallbox's
entity page if you want a live estimate.
**Estimated charging cost, last charge** keeps the last completed cycle. Prices
are applied to the energy intervals in which they were observed. If a price
changes between two meter readings, that interval's energy is divided in
proportion to time. This is an estimate, not a billing-grade interval meter.
The whole charge is never multiplied by its final price.

Missing prices, failed meter observations, long gaps and HA downtime make the
result incomplete instead of silently treating those intervals as free. An
incomplete cost is unknown. Both cost sensors expose `complete`; the last-cost
sensor also exposes `energy_kwh` and `unpriced_kwh` to describe coverage without
continually updating energy attributes on the running sensor. Additional
unobserved energy may exist during a partial running cycle. After completion,
the wallbox summary provides the whole cycle's energy. No historical prices are invented after a
restart. A completed result is stored once and is not repriced when settings
change later.

### Optional §14a EnWG network tariffs

The §14a network-tariff choices appear only when Home Assistant's country is
**Germany**. Fixed and dynamic electricity-price estimates remain available
in other countries.

These settings estimate the **variable network charge for charging energy**.
They do not enroll a tariff, implement grid-operator control or establish that
your installation meets the tariff's eligibility requirements.

Use your network operator's published prices, all on the same **gross EUR/kWh**
basis as the energy price. If a price sheet uses net cents/kWh, convert it first.
Choose one model:

| Model | Required information | Calculation |
|---|---|---|
| Module 2 | Regular network work price and whether the selected energy price already includes it | Replace the regular network component with 40% of that component. |
| Module 1 + 3 | Standard, high and low network prices; daily high/low windows; active quarters; tariff year | Replace the regular network component with the price for that local time. |

Whenever you open the Module 3 form, the integration makes a
**one-time lookup for that options flow**: it sends the configured HA Home coordinates to
[VNBdigital](https://www.vnbdigital.de/) to find the responsible low-voltage
network operator, then reads the current year's public data from
[Par14a-Database](https://github.com/the78mole/Par14a-Database). It sends no
vehicle or wallbox data. If there is exactly one operator and one published price
sheet with a daily schedule the form can represent, the gross prices and time
windows are filled in. The form shows the original price-sheet URL. Review
these values and the **price-includes-network** switch before saving.

If a price sheet is marked **provisional**, the form marks its source link
accordingly. Provisional figures may change. They are never saved without
your review and confirmation of the options flow.

The source is incomplete and some sheets use different windows in different
quarters. In those cases the form stays editable for manual entry; it does
not guess an operator or tariff. If the Module 3 standard rate differs from
the ordinary network rate, this model cannot represent the sheet correctly;
use a sensor supplying the complete price instead. The data is attributed
to Par14a-Database (the78mole), [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
The network operator's original price sheet governs. Reopen the settings at
any time to retrieve the current published values. **Automatic yearly refresh**
is an optional switch in the Module 3 form. When enabled, the integration
checks the new year's tariff on 1 January and applies it only if the operator
and published schedule are unambiguous and valid. If no usable sheet is
available, it retries once a day; cost estimates remain unknown for that year
until a valid tariff is found. A provisional sheet may be applied and is logged
as provisional. The next final sheet is not silently substituted during the
same year; reopen settings to review changes. The annual update changes only
the cost model and does not restart the Modbus connection or add Recorder
history.

If **Electricity price already includes the regular network price** is enabled,
the regular component is subtracted before the selected module's component is added.
Otherwise the input price must exclude that component, and the module's network
price is added. All other variable charges must already be present in the input
price. In Module 2 the **entire electricity price is never multiplied by 0.4**.

Module 2 requires separate metering/market location. Module 3 supplements
Module 1 and cannot be combined with Module 2 for the same market location.
Eligibility and the price structure are explained by the
[Bundesnetzagentur](https://www.bundesnetzagentur.de/DE/Vportal/Energie/SteuerbareVBE/artikel.html).

The Module 3 form supports multiple high-price and low-price daily windows.
Enter one period per line as `HH:MM-HH:MM`, for example `06:00-09:00` and
`17:00-20:00` on separate lines. Windows may cross midnight; all other times
use the standard rate. Window boundaries must fall on quarter hours, must not
overlap and must leave some time
at the standard rate. Select **two to four quarters** from the operator's
schedule. Outside those quarters, the standard rate applies. There are no
invented default prices, time windows or tariff year: copy these from the
published price sheet. German network-tariff windows always use
**Europe/Berlin**, including daylight saving changes. Enter the clock times
shown on the operator's sheet without converting them to Home Assistant's
display time zone. Manual entry, imported values and annual refresh all use
this same tariff clock.
A different tariff year makes the price unknown until you update the settings.
The form accepts up to eight high and eight low periods. For more complex
published schedules, use a sensor supplying the complete correct price instead.

The selected tariff must match the operator's published schedule and your
contract. The integration validates the input structure, not legal eligibility.
See the [Bundesnetzagentur tariff FAQ](https://www.bundesnetzagentur.de/DE/Beschlusskammern/BK08/BK8_06_Netzentgelte/68_Para14a_EnWG/FAQ/artikel.html).

Annual Module 1 relief, supplier standing charges and metering fees are not
arbitrarily spread across individual charges. With PV or a home battery,
wallbox energy is not necessarily grid-import energy; the configured price
still values all observed wallbox kWh. No PV/grid cost split is inferred.

## Storage and restarts

The latest complete wallbox cycle, latest cost result, and bounded loss totals
with one correctable latest match are retained outside Recorder. There is no
growing list of sessions, raw vehicle history or price history. The current
pricing interval keeps at most 256 price changes; overflow makes the estimate incomplete.

Loss values change only when an assignment or completed session changes. Costs
are rounded to cents and the last-cost energy attributes to 0.01 kWh. No extra
long-term statistics are created. The current-cost entity is disabled by
default; leaving it disabled keeps only the final cost in Recorder.

The app can continue observing a wallbox cycle while HA restarts. A later
BavarianData history match may still estimate that cycle's losses. Cost
calculation runs in HA, so a running app cannot reconstruct a complete cost
across HA downtime, even with a fixed price. A service restart also loses the
uncompleted session anchor. Previously completed results remain stored. Storage
problems affect these optional estimates, not heartbeat or charging commands.
