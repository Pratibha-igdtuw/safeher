# SafeHer — AI Guardian for Women's Safety

Hackathon prototype for the **Social Impact & Inclusion** category.

## What it does

SafeHer is a "predictive + passive" safety companion — instead of relying only
on the user to manually raise an alarm, it can detect distress and act on
its own.

| Feature | What it does |
|---|---|
| **SOS Button** | One tap sends live location + alert message to all trusted contacts |
| **Fake Call** | Schedules a realistic incoming call with a spoken script (uses the browser's built-in text-to-speech) to help exit an uncomfortable situation |
| **Voice Distress Detection** | Listens via the browser's speech recognition, transcribes speech, and an AI-style keyword/pattern model flags distress phrases — auto-triggers SOS above a confidence threshold |
| **Safe Check-in Timer** | Set a timer before walking somewhere; if you don't confirm you're safe before it runs out, an SOS auto-fires |
| **Route Safety Score** | Scores a route 0–100 using time-of-day + incident-density heuristics, color-coded Safe / Caution / High Risk |
| **Trusted Contacts** | Add/remove the people who get notified during an SOS |
| **Alert Log** | Shows recent alerts triggered (manual, voice, or check-in timeout) |
| **Safety Map** (SafetiPin-style) | Interactive Leaflet map — tap any spot to run a 6-parameter Safety Audit (lighting, openness, walkpath, security, transport, crowd); pins are color-coded by score |
| **Nearby Safety Directory** | Closest police stations, hospitals, pharmacies, and helplines, sorted by distance, with one-tap call |
| **Guardian Live Location Sharing (Bubble)** | Toggle to continuously share live location with your trusted circle on a live map, with a Private Mode to pause without ending the session — inspired by I'M SAFE's Bubble |
| **Anonymous Record** | Silently record audio evidence with no alert sent and nothing uploaded — stays on your device, playback + download only |
| **Community Safety Feed** | Post and browse crowd-sourced alerts, safe-spot tips, and incident reports tagged by area — inspired by I'M SAFE's Community Feed |

## Setup

```bash
cd safeher
pip install -r requirements.txt
python app.py
```

Open **http://127.0.0.1:5000** in Chrome (best support for speech
recognition + speech synthesis).

No API keys are required to run the full demo — everything works in
**mock mode** out of the box.

## Going from demo to production

Every "fake" integration point is isolated in one file so it's a clean
upgrade path after the hackathon:

- `utils/alerts.py` — set `MOCK_MODE = False` and add Twilio credentials to
  send real SMS instead of printing to the console.
- `utils/route_safety.py` — swap the simulated incident table for a real
  Google Maps Directions API call + a real crowd-sourced incident dataset.
- `utils/distress_detector.py` — currently a keyword/pattern classifier on
  the browser's speech-to-text transcript. Swap in a trained audio model
  (e.g. a fine-tuned YAMNet/CNN on scream + distress-keyword audio) that
  takes the raw audio stream directly for higher accuracy and to work even
  when words aren't clear.
- `utils/safety_services.py` — currently a small mock directory. Swap in a
  real Google Places "nearby search" call (police/hospital/pharmacy) to
  return real, live nearby services anywhere in the world.
- The Safety Map already uses real OpenStreetMap tiles via Leaflet (no API
  key needed) — the audits table is ready to scale into a real
  crowd-sourced dataset like SafetiPin's.

## Suggested demo flow (for judging)

1. Add a trusted contact (use your own phone to show the alert land).
2. Hit the SOS button live — show the location + alert being logged instantly.
3. Trigger the fake call — let it "ring", accept it, let the AI voice talk.
4. Say "help" or "bachao" into the mic — show it auto-detect distress and
   fire an SOS with no button press.
5. Start a 1-minute check-in timer, let it expire — show the automatic alert.
6. Switch to the **Safety Map** tab — tap a spot, submit a Safety Audit,
   watch the color-coded pin appear live.
7. Switch to **Directory** — show nearby police/hospital/pharmacy sorted by
   distance with a tap-to-call button.
8. Switch to **Guardian** — start live location sharing (Bubble map), show
   the "last shared at ..." status updating, then toggle Private Mode.
9. Trigger **Anonymous Record** on Home — record a few seconds, play it
   back, and point out no alert was sent and nothing left the device.
10. Switch to **Community** — post a safety alert or safe-spot tip and show
    it appear instantly in the feed.

## Tech stack

- **Backend:** Flask + SQLite
- **Frontend:** HTML/CSS/vanilla JS, Web Speech API (recognition + synthesis),
  Leaflet.js + OpenStreetMap for the Safety Map
- **AI components:** pattern-based distress classifier (pluggable with a
  real audio ML model), heuristic route-risk scoring model, crowd-sourced
  safety audit scoring
- **Pluggable integrations:** Twilio (SMS), Google Maps/Places (routing,
  nearby services)

## Why this fits Social Impact & Inclusion

- Addresses a direct, everyday real-world safety problem
- Voice-based interaction makes it accessible to low-literacy users
- Works offline/mock for demo, but architecture is ready for real deployment
  at campus or city scale
- Community angle: incident data model can grow into a crowd-sourced safety
  heatmap over time
