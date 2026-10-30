# Jarvis v3 – Feature-Backlog (fehlende Features aus v2 & älter)

Stand: 2026-10-30. Basis für die Weiterentwicklung ist **jarvis_v3**: Diese Version läuft auf dem Pi als `jarvis.service`, der Code ist dort byte-identisch mit dem lokalen Ordner.
Quelle für die Portierung ist **jarvis_v2** (lokal = `~/jarvis` auf dem Pi, inkl. der uncommitteten Änderungen).

## Versionen im Überblick

| Ordner | Was es ist | Status |
|---|---|---|
| `jarvis_v3` | Neubau vom Juli 2026, reines Gemini Live (`gemini-3.1-flash-live-preview`), MCP, Core-Memory als Markdown | **Läuft auf dem Pi**, ist aber **nicht in Git** |
| `jarvis_v2` | Hybrid: Gemini Live nur als Sprach-Frontend, alles wird per `delegate_to_backend` an das REST-„Slow Brain" delegiert, dazu Mem0 | Git = `origin/main` (19d8629, gepusht) + uncommittete Änderungen (openWakeWord statt Porcupine usw.). Liegt inaktiv in `~/jarvis` auf dem Pi |
| `jarvis_v2 - Copy` | Zwischenstand vom 22.02. (af6bebf + Voice-Bonnet-V2-Port) | vollständig in der v2-Historie enthalten |
| `jarvis` | alter Git-Klon auf af6bebf, dazu `history.json` (38 MB) | vollständig in der v2-Historie enthalten |
| `Jarvis-Project` | Backup des Pi-Home-Verzeichnisses (Dez 2025/Jan 2026), Git-Stand 48da2f8 | Vorgänger von v2. **Enthält Secrets** (`.env`, `.git-credentials`) |

## Was v3 schon kann

- Wake-Word „Hey Jarvis" (openWakeWord), Mikrofon-Watchdog (Timeout, Rapid-Fire, Stille, Queue-Stau)
- Gemini Live Voice-to-Voice, Rotation mehrerer API-Keys mit Fallback bei 429/Quota, Session endet nach Stille (Timer ab Ende der Sprachausgabe, unbeantwortete Sprache zählt nur begrenzt)
- LEDs (Voice Bonnet V2) und SFX als PCM (wake, sleep, thinking, success, error)
- Smart Home: generisches `call_ha_service` + alle HA-States und -Services im System-Prompt
- `set_speaker_volume`, `get_pi_temperature`, `end_conversation`
- Core Memory (`data/core_memory.md`, append/replace), `search_archive` (Stichwortsuche)
- MCP: `mcp.local` (SearXNG-Websuche, Firecrawl, Context7, Gmail, Google Calendar, Google Tasks, Mail, Fitbit/Google Health), `memory.local` (Basic Memory), HiFiBerry (Player, Queue, Library)
- `--test` Modus mit `trigger.tmp` (Text-Injection per `send_client_content`)

---

## P0 – Bugs & Quick Wins

- [x] **Archiv wird nie geschrieben.** `memory.log_interaction()` existiert, wird aber nirgends aufgerufen. Auf dem Pi hat `data/archive.jsonl` nur 1 Zeile (aus `tests/manual/test_memory.py`), deshalb findet `search_archive` nie etwas.
  - Fix: In `core/llm.py → _receive_audio_loop` die `input_transcription`- und `output_transcription`-Fragmente pro Turn sammeln und bei `turn_complete` `memory.log_interaction(user, jarvis)` aufrufen. Am besten in einem Thread, damit die Audio-Schleife nicht blockiert.
  - Vorlage: v2 `core/live.py` (Sammeln von `_current_user_transcript`) und v2 `services/memory.py → save_interaction`.

- [ ] **Prompt verweist auf ein Tool, das es nicht gibt.** Der System-Prompt in `core/llm.py` sagt bei „Musik": `category='station', name='Library Radio'`. Ein Tool mit diesen Parametern gibt es in v3 aber nicht (siehe P1 „Musik gezielt abspielen"). Entweder das Tool portieren oder die Prompt-Regel anpassen.

- [ ] **`end_conversation` beendet zu leicht.** Im Test hat Gemini die Session nach einem falsch transkribierten Fragment („ist") beendet. Die Regel in `core/llm.py` (KONVERSATIONSSTEUERUNG) und die Tool-Beschreibung in `core/tools.py` enger fassen: nur bei einem klaren Abschluss, bei unklaren Fragmenten still bleiben. Das Sessionende per Inaktivität reicht sonst.

- [x] **Hardware-Button verdrahtet.** Ein Druck während einer Session bricht sie ab (Wiedergabe stoppt, LED kurz rot). Im Idle blinkt die LED kurz in einer zufälligen Farbe. Umgesetzt über `GeminiLiveProvider.request_cancel()` (`cancel_event`, geprüft in `_inactivity_loop` bzw. beim Verbindungsaufbau). Pin per `JARVIS_BUTTON_PIN`, Backend `gpiozero` + `lgpio` (RPi.GPIO kann auf Kernel 6.12 keine Flanken erkennen). Wenn es Timer gibt, soll der Button auch einen klingelnden Alarm stoppen (siehe P1).

## P1 – Alltagsfeatures mit hohem Nutzen

- [ ] **Timer & Wecker.** In v3 gibt es gar keine Timer.
  - v2: `services/timer.py` (`manage_timer_alarm`, `background_timer_check`, `play_alarm_sound`, `stop_alarm_sound`, Auto-Stopp nach `ALARM_TIMEOUT = 120` s). Gestoppt wird per Wake-Word oder Button, die LED atmet dabei orange.
  - Für v3: `assets/sounds_src/alarm_gentle.ogg` mit `scripts/convert_sounds.py` nach `assets/sounds/alarm.pcm` konvertieren und als Loop über `SFXManager` abspielen. Timer-Thread in `main.py` starten. Im Idle-Loop: Wake-Word bei aktivem Alarm = Alarm stoppen statt Session starten.
  - Mehrere Timer erlauben (v2 erlaubte nur einen).

- [ ] **Musik leiser drehen während Jarvis zuhört/spricht (Media-Ducking).**
  - v2: `main.py → lower_volume` und `restore_volume` (liest `sensor.hifiberry_plexamp_volume`, halbiert die Lautstärke und stellt sie danach wieder her). Dazu ein SIGTERM-Handler, der beim Stoppen des Dienstes die Lautstärke zurücksetzt und die LEDs ausschaltet.
  - Für v3: vor `llm_provider.connect()` absenken, im `finally` wiederherstellen. Alternativ direkt über das HiFiBerry-MCP oder die HA-`media_player`-Lautstärke.

- [ ] **Service-Antworten aus HA lesen (`return_response`).** Das schaltet gleich mehrere Features frei. `call_ha_service` kann aktuell nur auslösen, aber keine Daten zurückgeben.
  - Erweiterung: optionaler Parameter `return_response: bool` → `POST /api/services/{domain}/{service}?return_response=true` und das JSON zurückgeben.
  - Damit funktionieren: `weather.get_forecasts` (Wettervorhersage stündlich/täglich, inkl. „Regnet es gleich? Kann ich Rad fahren?"), `todo.get_items` (Einkaufsliste vorlesen), `calendar.get_events`.
  - v2 als Vorlage: `services/ha.py → get_weather_forecast` (inkl. Fallback auf das `forecast`-Attribut und Begrenzung auf 12 h bzw. 5 Tage).

- [ ] **Einkaufsliste (hinzufügen, abhaken, vorlesen).**
  - v2: `services/ha.py → manage_shopping_list` (REST `/api/shopping_list` und `shopping_list.add_item`/`complete_item`).
  - Mit dem `return_response`-Feature oben reicht vielleicht schon eine Prompt-Regel und `todo.*`-Services. Sonst als eigenes Tool portieren.

- [x] **Kurzzeitgedächtnis über Sessions hinweg.** In v3 startet jedes Wake-Word eine komplett frische Session. „Und wie wird es morgen?" zwei Minuten nach der Wetterfrage hat dann keinen Kontext mehr.
  - v2: `core/live.py → get_dynamic_system_instruction` hat die letzten 6 Turns in den Prompt gepackt, wenn die letzte Aktivität weniger als 15 min her war.
  - Für v3: Transkripte (siehe P0-Archiv-Fix) im RAM halten und beim nächsten `connect()` als Abschnitt „LETZTE KONVERSATION" anhängen. Alternativ die Session-Resumption der Live-API anschauen.

- [ ] **Inhalte aufs Handy schicken (`send_to_phone`).**
  - v2: `services/ha.py → send_notification` (`notify.notify` mit `clickAction`-URL, Bild, Priorität `high` → `channel: alarm_stream`) plus Prompt-Regeln: lange Antworten (Rezepte, Listen, Code), Bilder und Routen **proaktiv** aufs Handy schicken, ohne vorher zu fragen.
  - Theoretisch geht das auch über `call_ha_service`. Ein eigenes Tool mit klaren Parametern ist für das Live-Modell aber deutlich zuverlässiger.

- [ ] **Musik gezielt abspielen (Plex/Plexamp).**
  - v2: `services/ha.py → execute_play_music` (Kategorien artist/album/playlist/track/station, `plex://`-Content-IDs, „Library Radio" = feste Station-URL `plex://…/library/sections/6/stations/1`). Ohne Angabe wird automatisch der Plexamp-Player gewählt.
  - Prüfen, was das HiFiBerry-MCP schon abdeckt (`library_*`, `queue_add_track`, `playback_command`). Für „Library Radio" und Playlists braucht man wahrscheinlich weiter den HA-`play_media`-Weg.

## P2 – Nützlich, mittlerer Aufwand

- [ ] **Dynamische Persona- und User-Dateien (USER.md, ähnlich OpenClaw).** Baut auf dem Portabilitäts-Punkt „Persönlichen Prompt aus `core/llm.py` herauslösen" auf und sollte **vor** der Basic-Memory-Anbindung kommen, weil die sich in diese Prompt-Struktur einhängt.
  - Der Prompt wird aus Dateien zusammengesetzt, zum Beispiel `prompts/persona.md` und `prompts/USER.md` (gitignored, mit committeten `*.example.md`). Die allgemeinen Regeln bleiben im Code.
  - Offen: Wie weit Jarvis die Dateien selbst pflegt (wie heute `edit_core_memory`) und ob Core Memory darin aufgeht.
  - Dateien bei jedem `connect()` neu lesen statt nur beim Boot, damit Änderungen ohne Neustart greifen.

- [ ] **Basic Memory als optionales Wissen anbinden.** Ziel: Jarvis kennt das Wissen der anderen Agenten. Ohne `memory.local` in `core/mcp_config.json` muss alles wie bisher laufen, denn Basic Memory ist keine Voraussetzung für das Repo.
  - **Lesen:** Eine Prompt-Regel wie „Für Wissen über den User, Projekte oder Dinge, die andere Agenten wissen, nutze `bm_search`". Die Regel kommt nur in den Prompt, wenn die `bm_*`-Tools geladen sind.
  - **Fester Kontext:** Optional eine Hub-Note (zum Beispiel „Jarvis-Kontext") in den Prompt aufnehmen. Im Hintergrund cachen und regelmäßig aktualisieren, damit der Sessionstart nicht auf das MCP wartet.
  - **Schreiben:** Keine Rohgespräche, die bleiben im lokalen Archiv. Stattdessen einmal nachts aus `data/archive.jsonl` eine Tageszusammenfassung erzeugen und als Log-Note nach den Basic-Memory-Konventionen ablegen (vorher „README - Conventions" lesen, Ziel `logs/YYYY-MM-daily-digest`). Das läuft besser auf einem Server als auf dem Pi, zum Beispiel auf dem Dev-Rechner, auf den `data/` ohnehin synchronisiert wird.

- [ ] **Remote-Mailbox (Text an Jarvis aus Home Assistant).**
  - v2: `main.py` fragt alle 1,5 s `input_text.jarvis_chat` ab, leert das Feld, verarbeitet den Text und spricht die Antwort.
  - In v3 sind die Helfer schon da (`core/ha.py → read_mailbox`, `clear_mailbox`), es fehlt nur die Schleife. Den Text kann man wie im `--test`-Modus per `send_client_content` in eine Live-Session injizieren.
  - Offen: Soll die Antwort gesprochen werden oder nur stumm erscheinen? v2 hatte dafür `<SILENT>`. Wenn der User nicht zu Hause ist, eher per Push aufs Handy.

- [ ] **Letzte Antwort für das HA-Dashboard (`sensor.jarvis_last_response`).**
  - v2: `ha.set_state("sensor.jarvis_last_response", text[:250], attributes={"full_text": text})` nach jeder Antwort.
  - In v3 gibt es `core/ha.py → set_state` schon. Nur bei `turn_complete` mit dem Output-Transkript aufrufen.

- [ ] **HA-Verlauf abfragen (`get_ha_history`).** Fragen wie „Wann war ich gestern zu Hause?" oder „Wie lange lief der Receiver?".
  - v2: `services/ha.py → get_ha_history` (`/api/history/period`, UTC→Lokalzeit, max. 100 Events pro Entity mit Warnhinweis bei Kürzung).

- [ ] **Outdoor-Routen (Komoot).**
  - v2: `services/navigation.py → handle_route_planning` (Nominatim-Geocoding, OSRM-Fahrradzeit, Komoot-URL für rennrad/fahrrad/mtb/wandern/joggen, Start = Zuhause). Danach `send_to_phone` mit dem Link.

- [ ] **Jarvis per Sprache neu starten (`restart_service`).**
  - v2: `services/system.py → restart_service` (`sleep 5; sudo systemctl restart jarvis.service`, detached). Trivial zu portieren.

- [ ] **Standorte der Personen mit Adresse.** In v3 landen die `person.*`-Entities zwar mit lat/lon im Prompt, aber ohne lesbare Adresse.
  - v2: `services/ha.py → get_all_person_locations` und `get_entity_address` (holt die zugehörige `geocoded_location` per HA-Template).

- [ ] **Speicher-Eintrag löschen.** In v3 gibt es nur append/replace. „Vergiss X" klappt höchstens über replace mit einem leeren Text.
  - Vorlage: `Jarvis-Project/services/memory.py → delete_memory`. In v3 einfach eine Aktion `delete` in `edit_core_memory` ergänzen.

## P3 – Experimentell bzw. schwer

- [ ] **Autonome Wakeups (Jarvis meldet sich von selbst).**
  - v2: `system.schedule_wakeup` (zeitbasiert), `ha.create_ha_automation` (zustands-, numerisch- oder geo-basiert, legt eine HA-Automation an, die `INTERNAL_WAKEUP_TRIGGER|id|grund` in die Mailbox schreibt), `ha.delete_ha_automation`, ein Tageslimit und eigene Prompt-Regeln (nachts nicht ansprechen, lieber Push).
  - **Voraussetzung:** Remote-Mailbox (P2). Hinweis: In v2 war das zuletzt per `WAKEUP_COUNT < 0` deaktiviert.

- [ ] **Nächtliches „Dreaming" und Routine-Analyse.**
  - v2: `services/routine.py → analyze_routine` (HA-Verlauf der letzten 48 h für Handy, Schlaf, Receiver, Person und Zonen, POI-Auflösung per Google-Suche, Tages-Journal in Mem0) und `get_habits_summary` (LLM abstrahiert daraus Gewohnheiten). Wurde täglich um 4 Uhr ausgelöst. Die Gewohnheiten landeten im System-Prompt.
  - **Nicht wieder mit Mem0, ChromaDB und LiteLLM auf dem Pi bauen** (siehe Hinweise unten). Besser serverseitig oder über das Basic-Memory-MCP (`bm_write`, `bm_search`), das schon angebunden ist.

- [ ] **Maps-/POI-Suche** („Nächster Italiener?", „Hat der Baumarkt offen?").
  - v2: `services/google.py → perform_maps_search` (Gemini mit `googleMaps`-Grounding und `latLng` = Zuhause). Websuche ist in v3 über SearXNG abgedeckt, Maps nicht.

- [ ] **Python-Code ausführen** (Berechnungen, API-Abfragen).
  - v2: `services/system.py → run_local_python` (`exec` mit `requests`, `math`, `datetime`). Großteils durch Firecrawl und SearXNG ersetzt. Falls doch gewünscht: prüfen, ob das Live-Modell das eingebaute Code-Execution-Tool unterstützt, statt `exec` auf dem Pi.

- [ ] **Token- und Kosten-Logging pro Session.**
  - v2: `core/live.py` summiert `usage_metadata` und gibt am Ende geschätzte € aus.

- [x] **Semantische Archivsuche** statt Stichwortsuche. Umgesetzt mit Gemini-Embeddings (`gemini-embedding-001`, 256 Dim.) in `data/archive_vectors.jsonl`, Stichwort-Treffer als Bonus und als Fallback ohne API. Bestehende Einträge nachträglich einbetten: `python -m scripts.backfill_embeddings`. (Basic Memory wurde bewusst nicht genommen, damit die Gesprächslogs nicht im geteilten Wissensspeicher landen, siehe P2 „Basic Memory als optionales Wissen anbinden".)
  - [ ] Ähnlichkeitsschwelle `SEMANTIC_MIN_SCORE = 0.70` in `core/memory.py` ist aus wenigen Beispielen abgeleitet. Nach ein paar Wochen Praxis prüfen, ob Jarvis zu viel oder zu wenig findet.

## Abgedeckt (kein Port nötig)

- Kalender lesen/erstellen → MCP `google_get_events` / `google_manage_event`
- Websuche → MCP `searxng_search` (+ `firecrawl_scrape`)
- Geräte schalten, Media-Steuerung, Gerätestatus → `call_ha_service` + Live-States im Prompt
- Systemlautstärke → `set_speaker_volume`
- Fakten merken → `edit_core_memory`
- API-Key-Rotation → `GeminiLiveProvider.rotate_key`
- Neu in v3 (gab es in v2 nicht): Gmail, Google Tasks, Fitbit/Google Health, Basic Memory, HiFiBerry-Library

---

## Hinweise für die Umsetzung

- **Neues Tool in v3:** `types.FunctionDeclaration` in `core/tools.py → FUNCTION_DECLARATIONS` ergänzen, die Funktion in `TOOL_IMPLEMENTATIONS` eintragen und eine Regel im Prompt in `core/llm.py` hinzufügen.
- **Lokale Tools blockieren die Audio-Schleife:** `_receive_audio_loop` ruft `tools.execute_tool()` synchron auf. Langsame Tools (HA-Verlauf, Routen, Wetter) besser mit `await asyncio.to_thread(tools.execute_tool, name, args)` ausführen.
- **RAM ist knapp:** Der Pi Zero 2W hat 463 MB, Jarvis belegt davon schon ~60 % und der Pi swappt. Heute hat er mehrere Minuten nicht auf SSH reagiert und lief danach frisch gebootet. Also keine schweren Libraries wie Mem0, ChromaDB oder LiteLLM zurückholen, sondern Rechenarbeit auf Server/MCP auslagern.
- **Viele MCP-Tools in jeder Session:** Gateway (32) + Basic Memory (12) + HiFiBerry (30+) + lokale Tools ergeben ~80 Tool-Definitionen pro Live-Session. Das kostet Tokens und Latenz und verschlechtert die Tool-Auswahl. Eine Allow- oder Denylist pro Server in `core/mcp_config.json` lohnt sich. HiFiBerry hat außerdem destruktive Tools (`library_delete_album`, `library_delete_track`), die Gemini besser nicht sehen sollte.
- **Daten aus v2 retten:** Auf dem Pi liegen in `~/jarvis/memories/` (8,2 MB) das Mem0-Archiv aller v2-Gespräche (`paul_archive`), das Tages-Journal (`paul_journal`) sowie `core.md` und `episodic.md`. Das vor dem Löschen von `~/jarvis` sichern und gegebenenfalls nach `archive.jsonl` oder Basic Memory migrieren (`scripts/migration/migrate_memories.py` als Startpunkt).

---

## Portabilität – damit das Repo auch bei anderen läuft

Ziel: Klonen, `.env` und Config ausfüllen, starten. Ohne Paul-spezifische Hosts, Entities oder Hardware-Annahmen im Code.

- [x] **`.env.example`** anlegen
- [x] **`requirements.txt` aufräumen.** Die Zeile `/home/pi/aiyprojects-raspbian` ist ein lokaler Pfad und wird in v3 gar nicht genutzt (kein `aiy`-Import mehr) → entfernt, ebenso die ungenutzten `pygame`, `beautifulsoup4`, `mem0ai` und `chromadb`. `smbus2` ist als optional markiert. (Die Migrationsskripte unter `scripts/migration/` brauchen bei Bedarf zusätzlich `chromadb`.)
- [ ] **Persönlichen Prompt aus `core/llm.py` herauslösen.** Folgendes steht dort fest im Code: „Paul", Münchner Lokalkolorit, die Yamaha-Sleep-Button-Regel, „Library Radio" und Plexamp/HiFiBerry. Das kommt nach `prompts/persona.md` (gitignored) mit committeter `prompts/persona.example.md`. Die allgemeinen Regeln bleiben im Code.
- [ ] **Werte konfigurierbar machen**, per Env oder Config-Datei:
  - Voice (`Zubenelgenubi`)
  - Mic-Gain (`* 4.0` in `_send_audio_loop`)
  - Wake-Word-Modell und Schwelle (`hey_jarvis`, 0.5)
  - Mailbox-Entity (`input_text.jarvis_chat` in `core/ha.py`)
- [ ] **MCP-Config:** `core/mcp_config.json` enthält private Hosts → `mcp_config.example.json` committen und die echte Datei gitignoren. Dabei gleich eine Allow-/Denylist pro Server einbauen.
- [ ] **Hardware-Abstraktion:** `BonnetHardware` setzt das Voice Bonnet V2 voraus (I2C-LED-Treiber auf 0x31, Button an GPIO 23). Das hinter ein kleines Interface legen, mit `NullHardware`, wenn nichts gefunden wird. Den Mock-Fallback ohne `smbus2` gibt es schon.
- [ ] **Audio-Geräte konfigurierbar machen:**
  - `deploy/asound.conf` hat `hw:CameraB409241` (PS3 Eye) und `hw:aiyvoicebonnet` fest verdrahtet.
  - `core/audio.get_bonnet_card_index` sucht nach `aiyvoicebonnet`.
  - Kartennamen per Env setzen, ohne Bonnet keine Mixer-Settings. Im README dokumentieren, welche Teile Hardware-spezifisch sind.
- [x] **Feste `/home/pi/jarvis_v3`-Pfade** in Hilfsskripten durch Pfade relativ zum Repo ersetzen. Betroffen: `convert_sounds.py`, `migrate_memories.py`, `dump_db.py`, `test_ha.py`, `test_wake.py`, `test_interrupt.py`.
- [x] **Repo-Struktur aufräumen:**
  - manuelle Testskripte nach `tests/manual/`
  - Einmal-Tools nach `scripts/` (Migration, `patch.py`, `bt.sh`, `pystack.sh`, Watchdog-Tests)
  - Planungsdokumente aus dem Rewrite nach `docs/` (`implementation_plan*.md`, `migration_guide.md`) oder löschen
- [ ] **Deployment ins Repo:**
  - [x] `deploy/jarvis.service` (+ `deploy/asound.conf`)
  - `deploy/install.sh` (venv anlegen, Pakete installieren, ALSA-Config einrichten, Service aktivieren)
- [ ] **Optionale Integrationen sauber deaktivierbar machen.** Ohne `HA_URL` keine HA-Tools und kein HA-Kontext im Prompt. Ohne MCP-Config keine MCP-Tools. Nichts davon darf den Start verhindern.
