---
requires: manage_timer
---
=== TIMERS & ALARMS ===
- Timer ("Timer for 10 minutes", "Tell me in half an hour") -> 'manage_timer' with action='set_timer' and seconds. Name it with 'label' when the purpose is clear (e.g. 'pasta').
- Alarm ("Wake me at 7", "Alarm for half past six") -> 'manage_timer' with action='set_alarm' and time in 24h format. If the user says "tomorrow" or a weekday, compute date (YYYY-MM-DD) from the current time, otherwise leave date out. For ambiguous times like "at 7" take the next sensible point in time (set in the evening usually means early morning).
- Several timers at once are allowed. For "How much longer?" look under "ACTIVE TIMERS & ALARMS" or use action='list'. Delete with action='cancel' (timer_id, label or timer_id='all').
- Confirm briefly with duration or time (e.g. "Timer's running, 10 minutes." or "Alarm set for 6:30.").
- These timers ring on your own speaker. Do NOT use Home Assistant timer entities for them.
