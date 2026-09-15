# FloodSafe flood-alert notifications

Push flood warnings to real phones, targeted by location.

**Nothing in this file contains a credential, and nothing in the repository does
either.** The setup below is the part only you can do — it needs a Firebase
project that belongs to you.

---

## What this is, honestly

- Alerts fire on a **transition into HIGH or EXTREME**, not on a level, and not
  on every dashboard refresh.
- There are **three separate paths**: REAL alerts from measured risk,
  SIMULATION demo alerts from the simulator, and TEST alerts from the button.
  Simulation and test pushes are labelled as demonstrations inside the
  notification itself.
- An alert reaches **only devices registered at the affected location**.
- "FCM accepted" means Firebase accepted the send request. It is **not** a
  confirmation that a phone displayed anything. The UI never says "delivered".
- FloodSafe runs completely normally with none of this configured.

---

## 1. Create the Firebase project

1. <https://console.firebase.google.com> → **Add project**. Analytics is not needed.
2. **Project settings → General → Your apps → Web** (`</>`), register an app.
3. Copy the `firebaseConfig` values it shows you.
4. **Project settings → Cloud Messaging**. If the API is disabled, enable
   "Firebase Cloud Messaging API (V1)".
5. Still on Cloud Messaging → **Web configuration → Web Push certificates** →
   **Generate key pair**. This is the **VAPID key**.
6. **Project settings → Service accounts → Generate new private key**. A JSON
   file downloads. **This is the secret.** Put it somewhere git ignores:

   ```
   mkdir secrets
   mv ~/Downloads/your-project-firebase-adminsdk-*.json secrets/floodsafe-service-account.json
   ```

   `secrets/` and `*service-account*.json` are already in `.gitignore`.

---

## 2. Configure FloodSafe

Copy `.env.example` to `.env` and fill in:

```bash
# secret — server only
FCM_SERVICE_ACCOUNT_FILE=./secrets/floodsafe-service-account.json

# public by design — served to the browser at runtime
FIREBASE_WEB_API_KEY=...
FIREBASE_AUTH_DOMAIN=your-project.firebaseapp.com
FIREBASE_WEB_PROJECT_ID=your-project
FIREBASE_MESSAGING_SENDER_ID=...
FIREBASE_APP_ID=...
FIREBASE_VAPID_KEY=...
```

Restart the backend and check:

```bash
curl http://127.0.0.1:8000/api/notifications/status
```

`"configured": true` means the server can send.

---

## 3. HTTPS is required

Web push only works in a **secure context**. `localhost` counts, but a phone on
your Wi-Fi reaching `http://192.168.x.x:5173` **does not** — the browser will
refuse to grant notification permission.

For a demonstration with real phones, pick one:

- **Deploy it** (Vercel/Render/Fly) and set `PUBLIC_DASHBOARD_URL` to that URL.
- **Tunnel it**: `cloudflared tunnel --url http://localhost:5173` or
  `ngrok http 5173`, then set `PUBLIC_DASHBOARD_URL` to the https URL it prints
  and add that origin to `CORS_ORIGINS`.

---

## 4. Register 2–5 phones

On **each** phone, over HTTPS:

1. Open FloodSafe.
2. Choose the place that phone represents:
   `State → District → Location` (e.g. Uttarakhand → Rudraprayag → Gaurikund).
3. In the **Flood alerts** card on the Dashboard, tap **Enable flood alerts**.
4. Accept the browser's notification permission prompt.
5. It should say `✅ Device registered for <location>`.

Each phone gets its own FCM registration token and its own database row.
Re-registering the same phone **updates** its row; it never creates a duplicate.

Check them in **Command centre → Flood alert devices**:

```
Active devices: 5
📱 Device 1  Gaurikund   fcm-to…a1b2
📱 Device 2  Gaurikund   fcm-to…c3d4
📱 Device 3  Gaurikund   fcm-to…e5f6
📱 Device 4  Rishikesh   fcm-to…7890
📱 Device 5  Joshimath   fcm-to…abcd
```

Only a masked fragment of each token is ever shown or logged — the full token is
a bearer credential for pushing to that phone.

---

## 5. Send a test alert

**Command centre → Flood alert devices → Send test alert.**

Every active phone should receive:

> 🧪 **FLOODSAFE TEST ALERT**
> This is a notification delivery test. No emergency is occurring.

Test alerts are always allowed, even in test mode. They are the safe thing to
use on stage.

---

## 6. Location targeting

With phones 1–3 at Gaurikund and phone 4 at Rishikesh, an alert for Gaurikund
reaches **1, 2, 3 only**. Phone 4 gets nothing.

Widen the audience with `ALERT_TARGETING_SCOPE`:

| Value | An alert reaches |
|---|---|
| `location` (default) | devices registered at that exact location |
| `district` | every device in the district |
| `state` | every device in the state |
| `radius` | devices within 25 km of the location |

---

## 7. How automatic alerts work

The risk engine is untouched. After each assessment of a monitoring location,
FloodSafe compares the level it just produced with the level stored previously:

| Transition | Alert? |
|---|---|
| LOW → MODERATE | no |
| MODERATE → **HIGH** | **yes** |
| HIGH → HIGH | no — the level did not change |
| HIGH → **EXTREME** | **yes** — it got worse |
| EXTREME → EXTREME | no |
| EXTREME → MODERATE | no — de-escalation is not an emergency |

Then a **cooldown** (`ALERT_COOLDOWN_MINUTES`, default 30) suppresses a repeat
alert for the same location *and* severity, so a score flapping across a class
boundary cannot re-notify every few minutes. An escalation to a *different*
level is a different key and is never suppressed by the cooldown.

Only real monitoring locations reach this path — risk-map grid cells never do.

---

## 8. Simulator demo alerts (the SIH demonstration)

When the simulator drives the **selected location** into HIGH or EXTREME, every
phone registered at **that location** receives a **real FCM push**, labelled as
a demonstration in both the title and the body:

> ⚠️ **FLOODSAFE HIGH SIMULATION ALERT**
> HIGH flash-flood risk simulated at Gaurikund, Uttarakhand.
> Risk Score: 64
> 🧪 SIH DEMONSTRATION — NOT A REAL EMERGENCY

> 🚨 **FLOODSAFE EXTREME SIMULATION ALERT**
> EXTREME flash-flood risk simulated at Gaurikund, Uttarakhand.
> Risk Score: 87
> 🧪 SIH DEMONSTRATION — NOT A REAL EMERGENCY

**How it works**

```
Simulator slider / scenario  (Dashboard, location = Gaurikund)
        -> POST /api/simulation/run  { overrides, location_id: loc_gaurikund }
        -> existing risk engine recomputes Gaurikund
        -> level is HIGH or EXTREME and not yet alerted this episode?
        -> phones registered at loc_gaurikund
        -> Firebase Cloud Messaging
        -> each phone
```

- The **existing** simulator and risk engine decide the level. There is no
  second formula.
- **Only the simulated location pages anyone.** Simulator overrides apply to
  every location, so the Command Centre would otherwise see Rishikesh and
  Joshimath go EXTREME too. Demo alerts come from the simulator endpoint for
  the one location you are simulating, never from that region-wide refresh.
- **One alert per level per simulation episode.** An episode runs from the first
  simulator change until **Exit simulation**.

| While simulating | Push? |
|---|---|
| 40 → 50 (MODERATE) | no |
| 50 → 61 (enters HIGH) | **HIGH demo alert** |
| 61 → 70 (still HIGH) | no |
| 70 → 81 (enters EXTREME) | **EXTREME demo alert** |
| 81 → 90 (still EXTREME) | no |
| EXTREME → back to HIGH | no |
| **Exit simulation** | no — exiting never notifies |
| New simulation → enters HIGH | **HIGH demo alert again** |

A failed send (network blip) is retried on the next slider change; a successful
one never repeats. Two slider moves arriving at once cannot double-send: the
check and the claim happen atomically in the database.

**Real alerts are unaffected.** `FLOOD_ALERT_TEST_MODE` still governs alerts from
real measured data only, and simulated risk still can never reach the real
alert path. To switch demo alerts off entirely: `SIMULATION_ALERTS_ENABLED=false`.

**The phones and the simulator must use the same location.** Targeting is by
exact location. If the phones registered at *Uttarakhand → Gaurikund*, run the
simulator at *Uttarakhand → Gaurikund* too — not at *Rudraprayag district →
Gaurikund*, which is a different location entry. The simulator panel says
"No phones are registered at …" when they do not match.

---

## 9. Sound, vibration and the Android settings that control them

**Sounds** (both generated by `scripts/make_alert_sound.py` from sine waves —
original work, no licensing question):

| File | Used for |
|---|---|
| `sounds/floodsafe-alert.wav` (1.3 s) | HIGH alerts, tests |
| `sounds/floodsafe-extreme-alert.wav` (3.0 s) | EXTREME alerts |

The EXTREME tone is a rapid two-tone warble (1175 / 740 Hz) and a rising sweep,
normalised close to full scale. It deliberately **avoids 853 Hz and 960 Hz**,
the frequencies used by official broadcast and wireless emergency alerts, so it
cannot impersonate a real government warning.

**What actually plays, honestly:**

| Phone state | Sound | Vibration |
|---|---|---|
| FloodSafe **open and on screen** | FloodSafe's own tone (EXTREME tone for EXTREME) | FloodSafe pattern via `navigator.vibrate` |
| FloodSafe in background / screen off | **Chrome's notification sound for this site** | Per the site's Android notification channel |

A website **cannot** set a custom sound for a background notification: the Web
Notifications API has no sound option, and a service worker cannot play audio.
On Android 8+ the sound, its volume and vibration for web notifications are
controlled by Chrome's **per-site notification channel**, which only the phone's
owner can change — and that is the strongest legitimate lever, below.

**Android channels:** a website cannot create its own Android notification
channel; channels belong to installed apps. Chrome creates one per site that
has notification permission, and FloodSafe's notifications use it.

EXTREME notifications are sent with `requireInteraction` (stays until dismissed
where supported), `renotify` (a replacement alerts again), the FloodSafe icon
and badge, a timestamp, and vibration pattern 500-200-500-200-1000 ms.

### Phone setup for the loudest legitimate alert (Android + Chrome)

Do this **once on each phone**, after tapping *Enable flood alerts*:

1. **Chrome site permission:** open FloodSafe → tap the padlock / ⓘ in the
   address bar → **Notifications: Allow**.
2. **Android → Settings → Apps → Chrome → Notifications**
   - Chrome notifications **on**.
   - Find the **Sites** section and the FloodSafe address (your
     `…trycloudflare.com` or deployment URL). Open it and set:
     - **Alerting** / **Default** (not Silent)
     - **Pop on screen** on
     - **Sound**: pick a loud tone the phone offers (an alarm-style ringtone
       works well for the demonstration)
     - **Vibration** on
     - **Show on lock screen** on
   - Menu names vary a little by manufacturer (Samsung, Xiaomi, OnePlus…).
3. **Volume:** turn up the **ring / notification** volume (background
   notifications) and the **media** volume (the FloodSafe tone when the page is
   open). They are separate sliders.
4. **Silent mode and Do Not Disturb off.** FloodSafe does not and cannot
   override them.
5. **Battery:** Settings → Apps → Chrome → Battery → **Unrestricted** (or "Don't
   optimise"), so Android does not delay pushes while the screen is off.
6. **Test on the phone itself:** on the Dashboard, **🔊 Test emergency sound**
   plays the EXTREME tone and vibration locally; **Send test alert** in the
   Command Centre checks the full push path.

If the page was reloaded, tap it once before the demo: browsers only allow a
page to play audio after the person has interacted with it. This is a browser
rule, and FloodSafe respects it rather than working around it.

---

## 10. Limitations — read before demonstrating

- **HTTPS is required.** `http://192.168.x.x` will not work.
- **Permission is required** and cannot be re-requested once denied; the user
  must re-allow it in browser site settings.
- **Silent mode and Do Not Disturb win.** Nothing here bypasses them, by design.
- **iOS**: web push needs iOS 16.4+ *and* the site added to the Home Screen.
- **Background delivery varies** by browser, OS and battery settings.
- **Custom sound only plays with the page open** (see above). With the page in
  the background the phone plays Chrome's per-site notification sound.
- **Vibration patterns** are honoured where the platform allows; on Android 8+
  the site's notification channel setting decides.
- **After updating FloodSafe**, open it once on each phone so the new service
  worker takes over.
- **FCM acceptance ≠ delivery.** The Command Centre says "FCM accepted" for
  exactly this reason.
- Dead tokens are **deactivated, not deleted**, so a phone that has become
  unreachable still shows in the list rather than silently vanishing.

---

## 11. Troubleshooting

| Symptom | Cause |
|---|---|
| "Firebase is not configured on the server yet" | `.env` missing/incomplete; check `/api/notifications/status` |
| "Web push needs HTTPS" | Serving over plain HTTP from an IP — use a tunnel |
| Permission prompt never appears | Already denied — reset in browser site settings |
| Test alert returns 503 | Server has no service account, or outbound network is disabled |
| Registered but nothing arrives | Check `FLOOD_ALERT_TEST_MODE`, and that the phone's location matches the alert's |
| No sound with the page open | Browser autoplay block — tap the page, then **Test emergency sound** |
| Simulator says "No phones are registered at …" | Phones registered at a different location entry — re-register at the exact location you are simulating |
| Demo alert sent once, not again | By design: one alert per level per episode. Press **Exit simulation**, then simulate again |
| Notification silent in background | Android → Apps → Chrome → Notifications → Sites → your FloodSafe URL → Alerting + Sound |
| Tapping the notification opens the wrong page | Open FloodSafe once on the phone so the updated service worker installs |

Server logs show the whole decision path, with tokens masked:

```
[SIMULATION] EXTREME risk reached
[ALERT] Location: Gaurikund
[ALERT] Risk score: 87
[ALERT] Target devices: 3
[FCM] Sending simulation notification
[FCM] Accepted: 3
[FCM] Invalid tokens: 0
```

---

## 12. The 2–5 phone demonstration, step by step

1. Laptop: start the backend, the frontend and the HTTPS tunnel.
2. Each phone: open the tunnel URL in **Chrome** → **Uttarakhand** →
   **Gaurikund** (or Rishikesh / Joshimath for the "should not receive" phones)
   → **Enable flood alerts** → **Allow** → do the phone setup in section 9.
3. Laptop: **Command centre → Flood alert devices** shows e.g.
   *Registered devices: 5 · Gaurikund: 3 · Rishikesh: 1 · Joshimath: 1*.
4. **Send test alert** — every phone should show 🧪 FLOODSAFE TEST ALERT.
5. Laptop: **Dashboard → Uttarakhand → Gaurikund**.
6. In the simulator raise rainfall / soil saturation / river level (or press
   **Simulate flash flood**).
7. Score crosses 61 → Gaurikund phones get **⚠️ HIGH SIMULATION ALERT**; the
   simulator panel shows **🚨 HIGH DEMO ALERT SENT · Target devices: 3 · FCM
   accepted: 3**.
8. Score crosses 81 → Gaurikund phones get **🚨 EXTREME SIMULATION ALERT**.
   Rishikesh and Joshimath phones get nothing.
9. **Exit simulation** — nobody is notified; live values return.
10. To demonstrate again, start a new simulation from step 6.

"FCM accepted" confirms Firebase took the request. Whether each phone displayed
it depends on that phone's settings — check the screens, not just the counter.
