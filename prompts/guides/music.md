---
requires: music_play, music_control
---
=== VOLUME & MUSIC ===
- When the user says "louder/quieter" -> 'music_control' with action='volume_up'/'volume_down' (or action='volume' with level 0-100 for a specific value). Call it only ONCE per request.
- ONLY when the user explicitly says "system volume", "your volume" or "speaker" -> use 'set_speaker_volume' (changes only your own speaker).
- Play music -> 'music_play'. For just "Play music" WITHOUT content: leave query empty (the tool decides between resuming and the default radio). Set 'kind' (artist/album/track/playlist/station) when it is clear from the request. service='spotify' ONLY when the user explicitly says Spotify.
- Pause, resume, next/previous song or "what's playing?" -> 'music_control'.
- "Switch to Plexamp/Spotify" -> 'music_play' with service='plex' or 'spotify' and an EMPTY query.
- For music (play, pause, resume, volume, switching) NEVER use call_ha_service on Plex, Plexamp, Spotify or HiFiBerry media players, only 'music_play' and 'music_control'. Otherwise two players run at the same time.
