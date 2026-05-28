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

## 🔧 Modified Functions in `rest.py`

- `_pin_relay_sync_outgoing_receipts_once()` (~line 587)
- `_notify_pin_relay_message_updated()` (~line 300)
- `mark_pin_relay_sent()` w `RestService` (~line 5083)
