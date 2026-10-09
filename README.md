# EV Away Power (evpwr)

Derives the **average electrical load of an EV over each away period** from the
change in state of charge (SOC) between departure and arrival — no presence
entity, no OBD, no charging API.

The result is written both as a sensor and as **long-term statistics that cover
the whole away window**, so graphs show a flat line from departure to arrival
instead of a single spike at the moment the car gets home.

## How a trip is defined

The car reports SOC meaningfully around departure and arrival, so two
consecutive SOC readings *are* the trip boundaries:

```
energy_kWh  = (soc_start - soc_end) / 100 * battery_capacity_kWh * usable_factor
avg_power_W = energy_kWh * 1000 / duration_hours
```

Worked example — leaves 07:00 at 60 %, returns 17:00 at 40 %, 60 kWh pack:

```
(60 - 40) / 100 * 60 = 12 kWh over 10 h  ->  1200 W average
```

This is the **average load over the whole away period** (driving *and* parked
time, vampire drain included). It is not instantaneous driving power, and the
sensor names say so.

## Install

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/Brandogg255/EVPWR`,
   category **Integration**.
2. HACS → Integrations → **EV Away Power** → Download.
3. Restart Home Assistant.
4. Settings → Devices & Services → **Add Integration** → *EV Away Power*.
5. Pick the SOC sensor, enter the pack capacity, save.
6. Optional but recommended: run the service `evpwr.backfill` (30 days) to make
   past trips visible immediately.

## Configuration

| Setting | Default | Meaning |
| --- | --- | --- |
| Name | `EV` | Device name; also seeds the statistic id |
| State of charge sensor | – | Sensor whose value is pack SOC in percent |
| Battery capacity | 60 kWh | Nominal pack capacity |
| Usable capacity factor | 1.0 | Degradation / unusable headroom multiplier |
| Minimum trip duration | 30 min | Shorter gaps are treated as a parked SOC refresh |
| Minimum SOC change | 1 % | Below this the reading is noise (1 % of 60 kWh = 0.6 kWh) |
| Maximum trip length | 30 days | Longer gaps mean the SOC reading went stale |
| Cumulative away-energy sensor | off | Adds a `total_increasing` kWh sensor for the Energy dashboard |

Settings can be changed later: the integration tile → **Configure**.

## Entities

For an entry named *EV*:

| Entity | Class | Meaning |
| --- | --- | --- |
| `sensor.ev_away_average_power` | power, W, measurement | Average load of the most recent completed away period |
| `sensor.ev_trip_energy` | energy, kWh, measurement | Energy lost during that period |
| `sensor.ev_away_energy_total` | energy, kWh, total_increasing | Optional running total of away energy |

Attributes on both trip sensors: `trip_start`, `trip_end`, `soc_start`,
`soc_end`, `soc_delta`, `duration`, `duration_minutes`, `energy_kwh`,
`avg_power_w`, `status`, `detail`, `statistic_id`, `anchor_soc`, `anchor_time`,
and `last_event_status` / `last_event_detail` for the most recent SOC change
even when it was rejected.

## Visualising the away period

The integration imports one hourly mean per hour of the away window into the
recorder's long-term statistics under the external statistic id
`evpwr:<name>_away_average_power` (shown in the `statistic_id` attribute).

```yaml
type: statistics-graph
title: EV away average load
period: day          # required: 5minute reads short-term stats, which we cannot import
stat_types: mean
entities:
  - entity: evpwr:ev_away_average_power
    name: Away average load
```

What each surface shows:

| Surface | Result |
| --- | --- |
| **Statistics graph card** with the `statistic_id` above | Flat line across the away window — this is the intended view |
| History / logbook (raw states) | Step change at arrival, because the sensor only changes state when the trip closes |
| `sensor.ev_away_average_power` in a statistics graph card | Step at arrival (recorder computes its own stats from the state change) |
| Energy dashboard | Only the optional cumulative `total_increasing` sensor; per-trip values are not accepted |

Home Assistant only accepts imported long-term statistics on **whole hour**
boundaries, so the flat line is one bucket per hour and a trip that starts or
ends mid-hour extends to the edges of its first and last bucket.

## Service: `evpwr.backfill`

Replays the recorded SOC history and re-imports every trip in the window. Use it
after installing, or after changing the battery capacity — it recomputes the
cumulative total, so running it twice does not double count.

```yaml
service: evpwr.backfill
data:
  days: 60
```

## Rejected trips

Every SOC pair is evaluated and reported in `status`:

| Status | Behaviour |
| --- | --- |
| `valid` | Published, statistics imported |
| `charging` | SOC increased (charged away from home) — excluded from consumption |
| `too_short` | Parked SOC refresh; the drift is folded into the open trip, its start time is kept |
| `noise` | SOC change below the configured resolution, or unchanged |
| `too_long` | SOC went stale rather than the car being away |
| `invalid_soc` / `invalid_duration` | Missing/non-numeric SOC, or a non-positive duration |

The open trip survives a Home Assistant restart: the anchor reading is stored in
`.storage/evpwr.<entry_id>.json`, so a restart mid-trip still closes the trip
correctly.

## Testing

```
python -m pytest -q
```

The trip arithmetic, rejection rules, hourly bucketing and the SOC chain are
unit tested without Home Assistant (`custom_components/evpwr/trip.py` is free of
HA imports on purpose).

To exercise the live integration without a real trip, drive a test SOC sensor
from an `input_number`:

```yaml
template:
  - sensor:
      - name: "Test SOC"
        unit_of_measurement: "%"
        device_class: battery
        state: "{{ states('input_number.test_soc') }}"
```

Then set `input_number.test_soc` from Developer Tools, point the integration at
`sensor.test_soc`, and watch `sensor.ev_away_average_power` plus the statistics
graph.

## Limitations

- Trip boundaries are the SOC **report** times, not the physical leave/arrive
  times. If the car reports SOC a few minutes after departure, the duration (and
  therefore the average) shifts accordingly.
- SOC resolution (usually 1 %) limits accuracy: 0.6 kWh per step on a 60 kWh
  pack, so short trips are noise by construction.
- Parked time at the destination is included in the average — by definition.
- Imported statistics are hourly; sub-hour trips collapse into one bucket.
