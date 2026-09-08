# FloodSafe flood-alert notifications

Push flood warnings to real phones, targeted by location.

**Nothing in this file contains a credential, and nothing in the repository does
either.** The setup below is the part only you can do — it needs a Firebase
project that belongs to you.

---

## What this is, honestly

- Alerts fire on a **transition into HIGH or EXTREME**, not on a level, and not
  on every dashboard refresh.
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

> 🧪 **FloodSafe TEST ALERT**
> This is a demonstration notification. No emergency is occurring.

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

## 8. Simulation safety

**Two independent guards, and the simulator can never get past either.**

1. `FLOOD_ALERT_TEST_MODE=true` (**the default**) — transitions are evaluated,
   targeted and recorded, but no real emergency push is sent.
2. **Simulated risk never sends a real emergency alert**, regardless of that
   setting. This is checked *before* test mode, so turning real alerts on can
   never also arm the simulator.

So you can drive Gaurikund to EXTREME on stage, watch the dashboard and the
Command Centre react, and page nobody.

To let real transitions actually notify phones, set
`FLOOD_ALERT_TEST_MODE=false`. Simulation stays blocked.

---

## 9. Sound and vibration

The FloodSafe tone is `frontend/public/sounds/floodsafe-alert.wav`, generated by
`scripts/make_alert_sound.py` from plain sine waves — original work, no
licensing question. Regenerate or replace it freely.

| Situation | What happens |
|---|---|
| Page open and focused | FloodSafe plays its own tone and vibrates |
| Page in background / closed | The **browser's** notification sound plays |

This is a platform limit, not an omission: **web push has no custom-sound
field**, and a service worker cannot reliably play audio. Android and iOS get
`sound: default` and high priority via the FCM payload.

Use **Test sound** in the Flood alerts card to check audio on each phone.
Browsers block audio until you have interacted with the page — tap once first.

Vibration uses `navigator.vibrate` where supported (Android Chrome, mostly).
iOS Safari and desktop generally ignore it. There is no fallback and no attempt
to work around it.

---

## 10. Limitations — read before demonstrating

- **HTTPS is required.** `http://192.168.x.x` will not work.
- **Permission is required** and cannot be re-requested once denied; the user
  must re-allow it in browser site settings.
- **Silent mode and Do Not Disturb win.** Nothing here bypasses them, by design.
- **iOS**: web push needs iOS 16.4+ *and* the site added to the Home Screen.
- **Background delivery varies** by browser, OS and battery settings.
- **Custom sound only plays with the page open** (see above).
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
| No sound with the page open | Browser autoplay block — tap the page, then **Test sound** |

Server logs show the whole decision path, with tokens masked:

```
[ALERT] HIGH risk transition detected (MODERATE -> HIGH) at Gaurikund, score 64
[ALERT] target devices: 3
[FCM] accepted=3 rejected=0 dead=0
```
