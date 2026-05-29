# BlackBerry PIN Messages — Experimental Patch

> [!WARNING]
> This is a **proof-of-concept** demo only. It has **no security measures** and may interfere with other PIM process functions (e.g., calendar or contact handling). **Use for testing purposes only.**

A quick experiment to restore PIN-to-PIN messaging on legacy BlackBerry devices.

---

## ✨ What Works

- **PIN Messaging** — exchange PIN messages between devices
- **Backend Delivery** — delivery status is reported back to the backend
- **Priority Flags** — importance markers are supported

> [!NOTE]
> Recipient-side *delivered/read* statuses are **not yet implemented**.

---

## 🖥 Backend — Build & Setup

### Build the relay server

```bash
go build -o pin-relay .
```

### Run the relay

```bash
./pin-relay -addr :8080 -db pin_messages.json
```

### PIN Emulator (optional)

If you need an emulator to test PIN message handling:

```bash
go build -o pin-emulator ./emulator/
```

```bash
./pin-emulator
```

---

## 📱 Device Installation

### 1. Configure the backend IP

In `rest.py`, replace all occurrences of `10.58.53.142:8080` with your own backend address.

### 2. Prepare the device

Create a data directory on the phone (e.g., `/var/pim_patch`), set permissions to `775`, and copy the source files into it.

### 3. Deploy

Run the following one-liner to compile, set permissions, create symlinks, and restart the PIM service:

```bash
rm -f /var/pim_patch/rest.pyc /var/pim_patch/PINMessageStatusListener.pyc; \
chmod 644 /var/pim_patch/rest.py /var/pim_patch/PINMessageStatusListener.py; \
on -u pim -e PYTHONHOME=/base/usr -e PYTHONPATH=/base/usr/lib:/base/usr/lib/pim/dependencies \
   -e LD_LIBRARY_PATH=/base/usr/lib:/base/usr/lib/pim/dependencies \
   /base/usr/bin/python3.2 -c "import py_compile; \
   py_compile.compile('/var/pim_patch/rest.py', cfile='/var/pim_patch/rest.pyc', doraise=True); \
   py_compile.compile('/var/pim_patch/PINMessageStatusListener.py', cfile='/var/pim_patch/PINMessageStatusListener.pyc', doraise=True)"; \
chmod 644 /var/pim_patch/rest.pyc /var/pim_patch/PINMessageStatusListener.pyc; \
ln -sPf /var/pim_patch/rest.py /base/usr/lib/pim/services/rest.py; \
ln -sPf /var/pim_patch/rest.pyc /base/usr/lib/pim/services/rest.pyc; \
ln -sPf /var/pim_patch/PINMessageStatusListener.py /base/usr/lib/pim/services/PINMessageStatusListener.py; \
ln -sPf /var/pim_patch/PINMessageStatusListener.pyc /base/usr/lib/pim/services/PINMessageStatusListener.pyc; \
/base/scripts/pim.sh stop 2>/dev/null; \
sleep 5; \
/base/scripts/pim.sh start >/tmp/pim_start.log 2>&1 & \
sleep 35; \
pidin ar | grep pimmain
```

---

## 📁 Repository Structure

```
├── pin-relay_with_pin-emulator/   # Go backend source
├── PINMessageStatusListener.py    # Status listener service script
├── rest_oryginal_decompiled.py     # Original (unmodified) PIM REST service — included for reference
├── rest.py                         # Patched version with PIN relay support
└── README.md                      # this file
```

> [!NOTE]
> `rest_oryginal_decompiled.py` is the **original, unmodified** PIM service file extracted from the device. All patches described below are applied directly onto `rest.py` during deployment.

---

## 🧱 Architectural Strategy — Transport-Layer-Only Replacement

### Goal

Replace the original BlackBerry BIS/BES relay transport **at the lowest possible point**, leaving the entire native PIM stack (state machine, outbox queue, retries, folder moves, notifications, conversations, `sync_id`/`refid` correlation) completely untouched.

### Hook Point

`send_PIN_message(self, jsonMsg)` — the boundary between Python-level PIM and the native PIN transport layer (likely `pinutils.so` / C extension / `libbps`).

This is the ideal seam because:
- the message already has a `sync_id`
- DB commit is done
- status is `SENDING`
- the listener is active

### Why NOT higher in the stack

| Layer | Problem |
|---|---|
| REST (`mail_message_new`) | Loses retry logic, outbox queue, status pipeline, folder moves, async handling, dedupe, attachment validation |
| `message.send_message()` | Account RPC shared across providers; PINProvider does native preprocessing; `sync_id` not yet generated |
| `message_send_post()` | Validation not complete; send queue not started; cold-boot resends bypassed |

### Implementation Options (recommended order)

1. **LD_PRELOAD hook** — intercept `send_PIN_message` as an ELF symbol (most elegant, very BlackBerry-style)
2. **Python extension monkey-patch** — replace `pinutils.send_PIN_message` at runtime (simpler to RE, but may be read-only / built-in)
3. **Socket-layer NOC emulation** — NOT recommended; requires crypto, framing, certs, BIS protocol — unnecessary complexity

### Status Callback Contract

The native `PINMessageStatusListener.add_status_update(refid, status)` must be preserved for Hub UX:

| Status | Code | Meaning |
|---|---|---|
| `TEMPORARY_FAILURE` | 2 | transient error → native retry |
| `ACCEPTED_BY_RELAY` | 5 | accepted → moves to Sent folder |
| `DELIVERED` | 6 | delivered → UI tick |
| `PERMANENT_FAILURE` | 1 | fatal → shown in Hub |

Because `sync_id == refid`, the backend only needs to echo the ID back and the entire native correlation system works.

### Resulting Data Flow

```
REST → message_internal → PINProvider → send_messages()
                                          → send_PIN_message()   ← HOOK
                                              → custom backend (websocket/http2/tcp)
                                              → callback status via PINMessageStatusListener
```

The Hub, notifications, retries, folders, conversations, and unread counts all remain 100% native. This is *resurrection engineering*: preserve the state machine and UX, replace only the transport.

## 🔧 Modified Functions in `rest.py`

- `_pin_relay_sync_outgoing_receipts_once()` (~line 587)
- `_notify_pin_relay_message_updated()` (~line 300)
- `mark_pin_relay_sent()` w `RestService` (~line 5083)
