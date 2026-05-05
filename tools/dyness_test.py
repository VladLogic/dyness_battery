"""
Dyness API Tester v3
Testet alle bekannten Endpunkte der Dyness Open API.

Verwendung:
    pip install requests
    python3 dyness_test.py

Nur API ID und Secret nötig — Gerät und Module werden automatisch erkannt.
Bitte Output als Issue auf GitHub teilen wenn du ein neues Dyness-Modell testest!
https://github.com/shopf/dyness_battery

WARNUNG: unBindSn ist deaktiviert! Niemals aktivieren ohne danach sofort
         bindSn erneut aufzurufen — sonst verlierst du den API-Zugriff.
"""

import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

import hashlib
import hmac
import base64
import json
import re
import requests
from email.utils import formatdate

# ===== HIER DEINE ZUGANGSDATEN EINTRAGEN =====
API_ID     = "DEINE_API_ID"
API_SECRET = "DEIN_API_SECRET"
API_BASE   = "https://open-api.dyness.com/openapi/ems-device"       # 🌍 Europa (Standard)
# API_BASE = "https://apacopen-api.dyness.com/openapi/ems-device"  # 🌏 Asia-Pacific

# Optional: Wenn Auto-Discovery fehlschlägt, hier manuell eintragen
DEVICE_SN  = ""   # z.B. R07ABCDEF123456XX-BMS  (leer lassen für Auto-Discovery)
DONGLE_SN  = ""   # z.B. R07ABCDEF123456XX       (leer lassen für Auto-Discovery)
# =============================================

SEP  = "=" * 60
SEP2 = "-" * 60


def get_md5(body: str) -> str:
    return base64.b64encode(hashlib.md5(body.encode("utf-8")).digest()).decode("utf-8")


def get_signature(secret: str, content_md5: str, date: str, path: str) -> str:
    sts = f"POST\n{content_md5}\napplication/json\n{date}\n{path}"
    return base64.b64encode(
        hmac.new(secret.encode("utf-8"), sts.encode("utf-8"), "sha1").digest()
    ).decode("utf-8")


def api_call(path: str, body_dict: dict) -> dict:
    url = f"{API_BASE}{path}"
    body = json.dumps(body_dict, separators=(',', ':'))
    date = formatdate(timeval=None, localtime=False, usegmt=True)
    md5 = get_md5(body)
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "Content-MD5": md5,
        "Date": date,
        "Authorization": f"API {API_ID}:{get_signature(API_SECRET, md5, date, path)}",
    }
    try:
        r = requests.post(url, headers=headers, data=body, timeout=15)
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def print_result(label: str, path: str, body: dict, result: dict):
    print(SEP)
    print(f"Endpunkt: {label}")
    print(f"Path: {path}")
    print(f"Body: {json.dumps(body, indent=2, ensure_ascii=False)}")
    print(SEP)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print()


def get_rt_points(result: dict) -> dict:
    """Wandelt realTime/data Liste in Dict {pointId: pointValue} um."""
    raw = result.get("data", []) or []
    return {item["pointId"]: item["pointValue"]
            for item in raw if isinstance(item, dict) and "pointId" in item}


def analyze_rt_points(pts: dict, label: str = ""):
    """
    Analysiert realTime/data Points strukturiert:
    - Bekannte Schlüssel-Points (Master + Modul)
    - getLastRunningDataBySn-relevante Felder
    - Alle vorhandenen Points gruppiert nach Bereich
    Gibt einen vollständigen Überblick für Debugging und neue Geräte.
    """
    if label:
        print(f"\n{'─'*60}")
        print(f"  Point-Analyse: {label}")
        print(f"{'─'*60}")

    if not pts:
        print("  ⚠️  Keine Points vorhanden.")
        return

    # ── 1. Schlüssel-Points (Master-Schema) ──────────────────────────────────
    master_keys = {
        # Standard-Schema (Junior Box, DL5.0C, PowerHaus, Stack100)
        "400":  "Batterieanzahl",
        "500":  "Batteriestatus",
        "600":  "Spannung (V)",
        "700":  "Strom (A)",
        "800":  "SOC (%)",
        "900":  "Leistung (W)",
        "1000": "Temp Max (°C)",
        "1100": "Temp Min (°C)",
        "1200": "SOH (%)",
        "1300": "Zellspannung Max (V)",
        "1400": "Zellspannung Min (V) / Tower SOC (%)",
        "1500": "Zellspannungsdiff (mV) / Tower SOH (%)",
        "1600": "Tower Remaining kWh",
        "1700": "Tower Rated kWh",
        "1800": "Zyklen / Tower Zyklen",
        "1900": "Gesamt geladen (kWh)",
        "2000": "Temp Max (°C) [Tower]",
        "2100": "?",
        "2400": "Zellspannung Max (V) [Tower]",
        "2700": "Zellspannung Min (V) [Tower]",
        "3000": "Temp Max (°C) [TP7]",
        "3300": "Temp Min (°C) [TP7]",
        # Junior Box spezifisch
        "4600": "PV Spannung (V)",
        "4700": "PV Strom (A)",
        "4800": "PV Leistung (W)",
        "5200": "OUT1 Spannung (V)",
        "5300": "OUT1 Strom (A)",
        "5400": "OUT1 Leistung (W)  [Junior Box: ggf. Summe beider Anschlüsse — unter Vorbehalt]",
        "5500": "OUT2 Spannung (V)",
        "5600": "OUT2 Strom (A)",
        "5700": "OUT2 Leistung (W)",
        "6600": "DOD (%)",
        "6900": "Heatsink Temperatur (°C)",
        "7000": "Luft Temperatur (°C)",
        "7100": "E-Batterie gesamt geladen (kWh)",
        "7200": "E-Charge heute (kWh)",
        "7300": "E-Batterie gesamt entladen (kWh)",
        "7400": "E-Discharge heute (kWh)",
        "7500": "PV E-Total (kWh)",
        "7600": "PV E-Heute (kWh)",
        # Powerbox G2 spezifisch
        "12300": "Temp Sensor Anzahl / SOH?",
        "12400": "SOC (%) [G2]",
        "12500": "Temperatur 1 (°C) [G2]",
        "12600": "Temperatur 2 (°C) [G2]",
        "12700": "Temperatur 3 (°C) [G2]",
        "12800": "Temperatur 4 (°C) [G2]",
        "13400": "SOC genau (%) [G2]",
        "13500": "Spannung (V) [G2/DL5.0C Modul]",
        "13600": "Remain capacity 1",
        "13800": "Module total capacity 1",
        "13900": "Zyklen [G2/DL5.0C Modul]",
        "14000": "Remain capacity 2 / SOC% [Modul]",
        "14100": "Module total capacity 2 / SOH% / Rated kWh [G2]",
        # Alarm Points (Tower Pro TP7 / Tower T14)
        # ACHTUNG: Points 4400-4900 sind geräteabhängig!
        # Bei Tower Pro TP7 → Alarm-Flag-Register
        # Bei Junior Box    → PV/Temperatur-Daten (gleiche Point-IDs, andere Bedeutung)
        "4400": "Alarm Flag1 [TP7] / ? [Junior Box]",
        "4500": "Alarm Flag2 [TP7] / ? [Junior Box]",
        "4600": "Alarm Flag3 [TP7] / PV Spannung (V) [Junior Box]",
        "4700": "Alarm Flag4 [TP7] / PV Strom (A) [Junior Box]",
        "4800": "Alarm Flag5 [TP7] / PV Leistung (W) [Junior Box]",
        "4900": "Alarm Flag6 [TP7] / ? [Junior Box]",
        "5001": "Alarm SpreadV [Tower T14]",
        "5002": "Alarm SpreadT [Tower T14]",
        "5003": "Alarm Insulation [Tower T14]",
        "5101": "Alarm AFE [Tower T14]",
        "5102": "Alarm BMS [Tower T14]",
        "5104": "Alarm SYS [Tower T14]",
    }

    found_master = {k: v for k, v in master_keys.items() if k in pts}
    if found_master:
        print("\n  ── Bekannte Schlüssel-Points ──")
        for pid, desc in sorted(found_master.items(), key=lambda x: (len(x[0]), x[0])):
            val = pts[pid]
            print(f"    {pid:>6}: {str(val):<15} ({desc})")

    # ── 2. Modul-Ebene ───────────────────────────────────────────────────────
    module_keys = {
        "10000": "Modul-SN",
        "10010": "Modul-SN [Stack100/TP7]",
        "10200": "Zellanzahl [DL5.0C]",
        "11000": "Modulnummer / Zellspannung",
        "11100": "Zellanzahl [TP7=30] / Zellspannung",
        "10300": "Cell 01 [DL5.0C]",
        "11200": "Cell 01 [Stack100/TP7/Tower]",
        "14200": "Temp Sensor Anzahl [TP7]",
        "14300": "Temperatur 1 [Stack100/TP7]",
        "14400": "Temperatur 2 [Stack100/TP7]",
    }
    found_module = {k: v for k, v in module_keys.items() if k in pts}
    if found_module:
        print("\n  ── Modul-Ebene Points ──")
        for pid, desc in sorted(found_module.items(), key=lambda x: (len(x[0]), x[0])):
            print(f"    {pid:>6}: {str(pts[pid]):<15} ({desc})")

    # ── 3. getLastRunningDataBySn relevante Felder ───────────────────────────
    # Diese kommen NICHT aus realTime/data sondern werden separat abgefragt.
    # Hier nur als Hinweis falls sie im pts-Dict vorhanden sein sollten.

    # ── 4. Alle Points — vollständige Liste nach Bereich ─────────────────────
    print("\n  ── Alle Points (vollständig) ──")
    numeric_pts = {k: v for k, v in pts.items()
                   if k not in ("T", "SUB", "TIME") and str(k).isdigit()}
    non_numeric = {k: v for k, v in pts.items()
                   if k not in ("T", "SUB", "TIME") and not str(k).isdigit()}

    # Meta
    if "T" in pts:
        print(f"    Zeitstempel: {pts['T']}")
    if "SUB" in pts:
        print(f"    SUB:         {pts['SUB']!r}")

    # Numerische Points sortiert
    ranges = [
        (0,    999,   "Master-Bereich (100–999)"),
        (1000, 4399,  "Master-Bereich (1000–4399)"),
        (4400, 9999,  "Alarm/Status-Bereich (4400–9999)"),
        (10000, 10299, "Modul-Identifikation (10000–10299)"),
        (10300, 11199, "Zellen DL5.0C (10300–11199)"),
        (11200, 14199, "Zellen Stack100/TP7/Tower (11200–14199)"),
        (14200, 15999, "Temperaturen Modul (14200–15999)"),
        (16000, 99999, "Sonstige (16000+)"),
    ]

    for r_start, r_end, r_label in ranges:
        in_range = {k: v for k, v in numeric_pts.items()
                    if r_start <= int(k) <= r_end}
        if in_range:
            print(f"\n    [{r_label}]")
            for pid in sorted(in_range.keys(), key=int):
                val = in_range[pid]
                known = master_keys.get(pid) or module_keys.get(pid) or ""
                suffix = f"  ← {known}" if known else ""
                print(f"      {pid:>6}: {str(val):<15}{suffix}")

    if non_numeric:
        print(f"\n    [Nicht-numerische Keys]")
        for k, v in sorted(non_numeric.items()):
            print(f"      {k}: {v!r}")

    # ── 5. Zusammenfassung interessanter Befunde ──────────────────────────────
    print(f"\n  ── Zusammenfassung ──")
    print(f"    Gesamt Points: {len(pts)}")

    # PV vorhanden?
    pv_pts = [k for k in ["4600", "4700", "4800"] if k in pts and pts[k] not in (None, "", "0", 0)]
    if pv_pts:
        print(f"    ✅ PV-Daten vorhanden: Points {', '.join(pv_pts)}")
        print(f"       PV Spannung={pts.get('4600','—')}V  Strom={pts.get('4700','—')}A  Leistung={pts.get('4800','—')}W")
        print(f"       ⚠️  Hinweis: Points 4600/4700/4800 sind geräteabhängig!")
        print(f"          Bei Junior Box = PV-Daten | Bei Tower Pro TP7 = Alarm-Flags")
        print(f"          Bitte im Kontext des Geräts interpretieren.")
    else:
        print(f"    —  Keine PV-Daten (Points 4600/4700/4800 nicht vorhanden oder 0)")

    # Energie-Zähler vorhanden?
    energy_pts = [k for k in ["7100","7200","7300","7400","7500","7600"] if k in pts]
    if energy_pts:
        print(f"    ✅ Energie-Zähler vorhanden: Points {', '.join(energy_pts)}")
    else:
        print(f"    —  Keine Energie-Zähler (7100–7600 nicht vorhanden)")

    # SOC-Schema erkennen
    if "800" in pts:
        print(f"    📋 SOC-Schema: Standard (Point 800 = {pts['800']}%)")
    elif "1400" in pts and pts.get("1400") and float(pts["1400"] or 0) <= 100:
        print(f"    📋 SOC-Schema: Tower/TP7 (Point 1400 = {pts['1400']}%)")
    elif "13400" in pts:
        print(f"    📋 SOC-Schema: Powerbox G2 (Point 13400 = {pts['13400']}%)")
    else:
        print(f"    ⚠️  SOC-Schema: UNBEKANNT — bitte als Issue melden!")

    # Alarm-Schema
    if "4400" in pts:
        print(f"    📋 Alarm-Schema: TP7/Tower (Flag-Register 4400–4900)")
    elif "5001" in pts:
        print(f"    📋 Alarm-Schema: Tower T14 (Bit-Points 5001–5104)")
    else:
        print(f"    📋 Alarm-Schema: Standard oder nicht vorhanden")

    print()


def analyze_running_data(result: dict):
    """
    Analysiert getLastRunningDataBySn Response.
    Zeigt alle Felder und markiert null-Werte explizit.
    Wichtig um zu erkennen ob ein Gerät Inverter-Daten liefert oder nicht.
    """
    print(f"\n{'─'*60}")
    print("  Analyse: getLastRunningDataBySn")
    print(f"{'─'*60}")

    data = result.get("data")
    if not data:
        print("  ⚠️  Keine Daten (data = null) — Gerät liefert keine Inverter-Daten")
        print("       → Firmware-Version wird in HA als 'Nicht verfügbar' angezeigt")
        print("       → Das ist normal für reine Batteriespeicher ohne Wechselrichter")
        return

    null_fields = []
    value_fields = []
    for k, v in data.items():
        if v is None or v == "":
            null_fields.append(k)
        else:
            value_fields.append((k, v))

    if value_fields:
        print("  ✅ Felder mit Werten:")
        for k, v in value_fields:
            print(f"    {k}: {v}")
    if null_fields:
        print(f"  ⚠️  Null-Felder ({len(null_fields)}): {', '.join(null_fields)}")
        if len(null_fields) == len(data):
            print("       → Alle Felder null — kein Wechselrichter verbunden")
            print("       → Firmware-Version wird in HA als 'Nicht verfügbar' angezeigt")
    print()


# ── Auto-Discovery ────────────────────────────────────────────────────────────
print(SEP)
print("Dyness API Tester v3 — Auto-Discovery")
print(SEP)
print()

device_sn = DEVICE_SN.strip()
dongle_sn = DONGLE_SN.strip()

if not device_sn:
    print("► Suche Geräte auf diesem Account...")
    sl = api_call("/v1/device/storage/list", {})
    print_result("Storage Geräteliste (Auto-Discovery)", "/v1/device/storage/list", {}, sl)
    code = str(sl.get("code", ""))
    if code in ("0", "200") or sl.get("code") == 0:
        devs = (sl.get("data", {}) or {}).get("list", [])
        if devs:
            bms = next((d for d in devs if str(d.get("deviceSn","")).endswith(("-BMS","-BDU"))), devs[0])
            device_sn = bms.get("deviceSn", "")
            dongle_sn = bms.get("collectorSn", "") or ""
            print(f"✅ Gerät gefunden: {device_sn}")
            if dongle_sn:
                print(f"   Dongle SN:    {dongle_sn}")
            if len(devs) > 1:
                print(f"   Weitere Geräte auf diesem Account:")
                for d in devs:
                    print(f"   - {d.get('deviceSn')} ({d.get('deviceModelName','?')}) / stationId: {d.get('stationId','?')}")
        else:
            print("❌ Keine Geräte gefunden. Bitte DEVICE_SN manuell eintragen.")
            sys.exit(1)
    else:
        print(f"❌ API Fehler: {sl.get('info')} — Bitte DEVICE_SN manuell eintragen.")
        sys.exit(1)
else:
    print(f"► Verwende manuell eingetragene SN: {device_sn}")

print()
body_sn   = {"deviceSn": device_sn}
body_full = {"deviceSn": device_sn, "collectorSn": dongle_sn} if dongle_sn else body_sn

# ── Gerät binden ──────────────────────────────────────────────────────────────
res = api_call("/v1/device/bindSn", body_full)
print_result("Gerät binden", "/v1/device/bindSn", body_full, res)

# ── Household Storage Detail ──────────────────────────────────────────────────
res = api_call("/v1/device/household/storage/detail", body_full)
print_result("Household Storage Detail", "/v1/device/household/storage/detail", body_full, res)

# ── Storage Liste (workStatus) ────────────────────────────────────────────────
res = api_call("/v1/device/storage/list", body_full)
print_result("Storage Geräteliste [liefert workStatus]", "/v1/device/storage/list", body_full, res)

# ── Anlageninfo ───────────────────────────────────────────────────────────────
res = api_call("/v1/station/info", body_sn)
print_result("Anlageninfo [batteryCapacity]", "/v1/station/info", body_sn, res)

# ── realTime/data Master ──────────────────────────────────────────────────────
res = api_call("/v1/device/realTime/data", body_full)
print_result("Echtzeit-Daten Master (realTime/data)", "/v1/device/realTime/data", body_full, res)

rt = get_rt_points(res)
analyze_rt_points(rt, "Master")

# SUB Point auswerten
sub_raw = rt.get("SUB", "")
battery_count = rt.get("400", "?")
print(f"► Point 400 (Batterieanzahl): {battery_count}")
print(f"► Point SUB (Sub-Module):     {sub_raw!r}")
print()

# Sub-Module ermitteln
sub_sns = []
if sub_raw:
    candidates = [s.strip() for s in str(sub_raw).split(",") if s.strip()]
    filtered = [s for s in candidates
                if not s.endswith(("-BMS", "-BDU"))
                and not re.search(r'-BDU-\d+$', s)]
    if len(filtered) > 1:
        sub_sns = filtered
        print(f"► {len(sub_sns)} parallele Sub-Modul(e) erkannt: {sub_sns}")
    elif len(filtered) == 1:
        print(f"► 1 Sub-Modul gefunden ({filtered[0]}) — kein separater Abruf nötig (Junior Box / Einzelmodul)")
    else:
        print("► Keine abfragbaren Sub-Module im SUB Point.")
else:
    print("► SUB Point leer — kein Multi-Modul Setup.")
print()

# ── Leistungsdaten ────────────────────────────────────────────────────────────
body_power = {"pageNo": 1, "pageSize": 3, "deviceSn": device_sn}
if dongle_sn:
    body_power["collectorSn"] = dongle_sn
res = api_call("/v1/device/getLastPowerDataBySn", body_power)
print_result("Letzte Leistungsdaten [SOC/Power]", "/v1/device/getLastPowerDataBySn", body_power, res)

# ── Sub-Module abfragen ───────────────────────────────────────────────────────
for sn in sub_sns:
    print(SEP)
    print(f"Sub-Modul: {sn}")
    print(SEP)
    m_body = {"deviceSn": sn}
    if dongle_sn:
        m_body["collectorSn"] = dongle_sn
    res = api_call("/v1/device/realTime/data", m_body)
    print_result(f"realTime/data Sub-Modul {sn}", "/v1/device/realTime/data", m_body, res)
    m_pts = get_rt_points(res)
    analyze_rt_points(m_pts, f"Sub-Modul {sn}")

# ── Weitere Standard-Endpunkte ────────────────────────────────────────────────
res_running = api_call("/v1/device/getLastRunningDataBySn", body_sn)
print_result("Letzte Betriebsdaten", "/v1/device/getLastRunningDataBySn", body_sn, res_running)
analyze_running_data(res_running)

for label, path, body in [
    ("Firmware-Version",       "/v1/device/checkVersion",      body_full),
    ("Energiedaten nach Datum", "/v1/device/getEnergyDataBySn",
     {**body_full, "date": "2026-03-12"}),
    ("Alarm-/Fehlerliste",     "/v1/alarm/query",              body_full),
    ("Gruppen-Liste",          "/v1/group/getGroupList",        {}),
    ("Safety-Code Liste",      "/v1/group/getSafelyList",       {}),
    ("System-Verknüpfungsliste", "/v1/group/getSystemList",     {}),
]:
    res = api_call(path, body)
    print_result(label, path, body, res)

print(SEP)
print("Test abgeschlossen!")
print("Bitte teile diese Ausgabe auf GitHub wenn du ein neues")
print("Dyness-Modell testest: https://github.com/shopf/dyness_battery")
print(SEP)
