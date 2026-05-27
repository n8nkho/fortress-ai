# System timezone

**All times on this stack are US Eastern (`America/New_York`). Nothing else.**

## Canonical source

| Item | Location |
|------|----------|
| Registry | `config/system_timezone.json` |
| Python API | `utils/system_time.py` |
| Env override | `FORTRESS_SYSTEM_TZ=America/New_York` |
| Cron | `CRON_TZ=America/New_York` at top of crontab (`scripts/install_production_crontab.sh`) |
| systemd | `TZ` + `FORTRESS_SYSTEM_TZ` in `deploy/*.service` |

## Rules for new code

1. Import `now`, `now_iso`, or `parse_iso` from `utils.system_time` — never `datetime.now(timezone.utc)`.
2. Call `ensure_system_tz()` at process entry (agents, cron wrappers, dashboards).
3. Cron schedules and RTH windows are expressed in US/Eastern.
4. JSON fields named `timestamp_utc` are legacy aliases; values are US/Eastern ISO-8601 with offset.
5. Prefer new field names: `timestamp`, `system_tz`.

## VM note

The host may run in UTC. That does not change system time — always use `system_time` or set `TZ`/`CRON_TZ`.
