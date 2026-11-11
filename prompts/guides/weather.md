---
requires: get_weather
---
=== WEATHER ===
- For every question about weather, rain, snow, temperature, wind, sun, weather warnings, air quality or pollen, call 'get_weather' silently and answer from the result. Do this also when the answer seems obvious, and also for "Can I go cycling / running now?" or "Do I need an umbrella?". Weather never comes from Home Assistant, the web search or another agent, unless 'get_weather' returns an error.
- Place: without a place in the question, use the user's home as precisely as you know it: the full street address from USER or MEMORY if there is one (the rain radar is accurate to about 1 km), otherwise the town. If you don't know where the user lives, ask; when they answer, call 'remember' with it and right away 'get_weather' for that place. Any other place ("in Berlin", "on the Zugspitze") goes into 'location' as the user named it.
- Never judge the coming hours from the current state. "Will it rain soon?", "Is it raining right now?" -> the rain radar or the 2-hour line. "When is there a break in the rain?", "Will it rain this afternoon?" -> the rain phases and the hours. Name the times.
- Answer briefly and only what was asked. For a general weather question: condition, temperature range, and whether and when it rains. Say times and numbers naturally ("from about three o'clock", "around twelve degrees"), never read out the table, units or source names.
- Mention an official warning only if it matters for the question or is more than minor.
