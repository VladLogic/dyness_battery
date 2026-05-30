"""Dyness Battery Integration för Home Assistant."""
import asyncio
import hashlib
import hmac
import base64
import json
import logging
import time
from email.utils import formatdate
from datetime import timedelta

import aiohttp
import async_timeout

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.const import Platform

_LOGGER = logging.getLogger(__name__)

DOMAIN = "dyness_battery"
PLATFORMS = [Platform.SENSOR]

# Entiteter som i tidigare versioner existerade men tagits bort.
# Rensas automatiskt ur Entity-Registry vid setup.
STALE_ENTITY_KEYS = {
    "alarmStatus1",
    "alarmStatus2",
    "alSpreadV",
    "alSpreadT",
    "alInsul",
    "alAfe",
    "alBms",
    "alSys",
}

# API Rate-Limit: max ~60 anrop/timme = 1/minut
_MIN_CALL_INTERVAL = 1.5
_RATE_LIMIT_BACKOFF = 10
_MAX_RETRIES = 3

# Giltiga BMS-suffix
_BMS_SUFFIXES = ("-BMS", "-BDU")

# ── Schema-konstanter ─────────────────────────────────────────────────────────
SCHEMA_TOWER        = "tower"
SCHEMA_STACK100     = "stack100"
SCHEMA_DL5          = "dl5"
SCHEMA_POWERBOX_PRO = "powerbox_pro"
SCHEMA_POWERBOX_G2  = "powerbox_g2"
SCHEMA_POWERDEPOT   = "powerdepot"
SCHEMA_JUNIOR       = "junior"
SCHEMA_CYGNI        = "cygni"
SCHEMA_UNKNOWN      = "unknown"

# Explicit modell → schema-mappning.
# Stack100 har inget hårdkodat modulantal här — det upptäcks dynamiskt vid körning
# och modellbeteckningen sätts till "STACK100-{n}S" baserat på faktiskt antal moduler.
# Alla Stack100-varianter fångas av prefix-match på "STACK100".
_MODEL_SCHEMA_MAP: dict[str, str] = {
    # Tower-familjen
    "TOWER-T14":        SCHEMA_TOWER,
    "TOWER-PRO-TP7":    SCHEMA_TOWER,
    "TOWER-PRO-TP11":   SCHEMA_TOWER,
    "TOWER-PRO-TP15":   SCHEMA_TOWER,
    "TOWER-TP7":        SCHEMA_TOWER,
    "TOWER-TP11":       SCHEMA_TOWER,
    "TOWER-TP15":       SCHEMA_TOWER,
    # Stack100 — prefix-match fångar alla varianter (7S, 8S, 10S, 12S, 13S, ...)
    "STACK100":         SCHEMA_STACK100,
    # DL5-familjen
    "DL5.0C":           SCHEMA_DL5,
    # PowerBox-familjen
    "POWERBOX-PRO":     SCHEMA_POWERBOX_PRO,
    "POWERBOX-G2":      SCHEMA_POWERBOX_G2,
    "POWERHAUS":        SCHEMA_POWERBOX_PRO,
    # PowerDepot G2
    "POWERDEPOT-G2":    SCHEMA_POWERDEPOT,
    # Junior Box
    "JUNIOR-BOX":       SCHEMA_JUNIOR,
    # Cygni hybrid-växelriktare
    "CYGNI":            SCHEMA_CYGNI,
}


def _detect_schema(device_model_name: str, rt: dict) -> str:
    """Schema-detektering: primärt via deviceModelName, fallback via Points.

    Stack100 har inga hårdkodade modellvarianter i kartan — prefix-match på
    "STACK100" täcker alla varianter oavsett modulantal.
    """
    model = (device_model_name or "").upper().replace(" ", "-")

    # Exakt match
    if model in _MODEL_SCHEMA_MAP:
        return _MODEL_SCHEMA_MAP[model]

    # Prefix-match för varianter (t.ex. STACK100-13S, CYGNI-10.0HS)
    for key, schema in _MODEL_SCHEMA_MAP.items():
        if model.startswith(key):
            _LOGGER.info(
                "Dyness: Modellvariant '%s' → schema '%s' via prefix-match ('%s')",
                model, schema, key,
            )
            return schema

    # Point-baserad fallback för helt okända modeller
    _LOGGER.warning(
        "Dyness: Okänd modell '%s' — schema-detektering via Points (fallback). "
        "Skapa gärna ett issue med loggfilen.",
        model,
    )
    # Stack100: Point 1100 (totalspänning) + 4300 (modulantal), saknar 800/1400
    if "1100" in rt and "4300" in rt and "800" not in rt and "1400" not in rt:
        return SCHEMA_STACK100
    if "1400" in rt and ("2400" in rt or "2700" in rt):
        return SCHEMA_TOWER
    if "800" in rt:
        return SCHEMA_JUNIOR
    if ("13400" in rt or "12400" in rt) and "800" not in rt and "1400" not in rt:
        return SCHEMA_POWERDEPOT
    return SCHEMA_UNKNOWN


def _scan_interval_for_modules(n: int) -> timedelta:
    """Dynamiskt scan-intervall baserat på modulantal."""
    if n <= 2:
        return timedelta(minutes=5)
    elif n <= 4:
        return timedelta(minutes=10)
    else:
        return timedelta(minutes=15)


def _get_gmt_time() -> str:
    return formatdate(timeval=None, localtime=False, usegmt=True)


def _get_md5(body: str) -> str:
    md5 = hashlib.md5(body.encode("utf-8")).digest()
    return base64.b64encode(md5).decode("utf-8")


def _get_signature(api_secret: str, content_md5: str, date: str, path: str) -> str:
    string_to_sign = (
        "POST" + "\n" + content_md5 + "\n" +
        "application/json" + "\n" + date + "\n" + path
    )
    sig = hmac.new(
        api_secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        "sha1"
    ).digest()
    return base64.b64encode(sig).decode("utf-8")


def _build_headers(api_id: str, api_secret: str, body: str, sign_path: str) -> dict:
    date = _get_gmt_time()
    content_md5 = _get_md5(body)
    signature = _get_signature(api_secret, content_md5, date, sign_path)
    return {
        "Content-Type": "application/json;charset=UTF-8",
        "Content-MD5": content_md5,
        "Date": date,
        "Authorization": f"API {api_id}:{signature}",
    }


def _to_float(v):
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def _is_success(result: dict) -> bool:
    """Kontrollerar om API-svar är lyckat — accepterar code som sträng eller int."""
    code = result.get("code")
    return str(code) in ("0", "200") or code == 0


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await _async_cleanup_stale_entities(hass, entry)

    coordinator = DynessDataCoordinator(
        hass,
        entry.data["api_id"],
        entry.data["api_secret"],
        entry.data["api_base"],
        device_sn=entry.data.get("device_sn"),
        dongle_sn=entry.data.get("dongle_sn"),
    )
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_cleanup_stale_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Tar bort föråldrade entiteter ur Entity-Registry."""
    from homeassistant.helpers import entity_registry as er

    entity_registry = er.async_get(hass)
    stale_entities = [
        entity
        for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id)
        if any(
            entity.unique_id == f"{entry.entry_id}_{key}"
            or entity.unique_id.endswith(f"_{key}")
            for key in STALE_ENTITY_KEYS
        )
    ]
    if stale_entities:
        _LOGGER.info(
            "Dyness: Rensar %d föråldrad(e) entitet(er): %s",
            len(stale_entities),
            [e.unique_id for e in stale_entities],
        )
        for entity in stale_entities:
            entity_registry.async_remove(entity.entity_id)
    else:
        _LOGGER.debug("Dyness: Inga föråldrade entiteter hittades.")


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


class DynessDataCoordinator(DataUpdateCoordinator):

    def __init__(self, hass, api_id, api_secret, api_base,
                 device_sn=None, dongle_sn=None):
        super().__init__(hass, _LOGGER, name=DOMAIN,
                         update_interval=timedelta(minutes=5))
        self.api_id     = api_id
        self.api_secret = api_secret
        self.api_base   = api_base
        self.device_sn  = device_sn
        self.dongle_sn  = dongle_sn

        self.station_info  = {}
        self.device_info   = {}
        self.storage_info  = {}
        self.realtime_data = {}
        self.module_data: dict[str, dict] = {}
        self.running_data: dict = {}

        self._bound: bool = False
        self._bound_sns: set = set()
        self._module_sns: list[str] = []
        self._last_call_time: float = 0.0
        self._storage_list_cycle: int = 0
        # Dynamisk modellbeteckning för Stack100 — sätts när modulantalet är känt.
        # Exempel: "STACK100-13S" när 13 sub-moduler detekteras.
        self._detected_model_label: str | None = None

    async def _call(self, session: aiohttp.ClientSession, path: str, body_dict: dict) -> dict:
        """Rate-begränsat API-anrop med retry vid HTTP 429."""
        elapsed = time.monotonic() - self._last_call_time
        if elapsed < _MIN_CALL_INTERVAL:
            await asyncio.sleep(_MIN_CALL_INTERVAL - elapsed)
        url = f"{self.api_base}/openapi/ems-device{path}"
        body = json.dumps(body_dict, separators=(',', ':'))
        for attempt in range(_MAX_RETRIES + 1):
            self._last_call_time = time.monotonic()
            headers = _build_headers(self.api_id, self.api_secret, body, path)
            try:
                async with session.post(url, headers=headers, data=body) as response:
                    if response.status == 429:
                        wait = _RATE_LIMIT_BACKOFF * (2 ** attempt)
                        _LOGGER.warning(
                            "Dyness: Rate-limit (429) på %s – Retry %d/%d om %ds",
                            path, attempt + 1, _MAX_RETRIES, wait,
                        )
                        if attempt < _MAX_RETRIES:
                            await asyncio.sleep(wait)
                            continue
                        return {}
                    raw_text = await response.text()
                    _LOGGER.debug("Dyness %s: %s", path, raw_text)
                    return json.loads(raw_text)
            except aiohttp.ClientError as e:
                _LOGGER.warning("Dyness %s anslutningsfel (försök %d/%d): %s",
                                path, attempt + 1, _MAX_RETRIES, e)
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise
        return {}

    def _update_scan_interval(self):
        """Anpassar scan-intervallet dynamiskt till modulantalet."""
        n = len(self._module_sns)
        new_interval = _scan_interval_for_modules(n)
        if self.update_interval != new_interval:
            self.update_interval = new_interval
            _LOGGER.info(
                "Dyness: %d modul(er) detekterade → scan-intervall satt till %d min",
                n, int(new_interval.total_seconds() / 60)
            )

    async def _async_update_data(self):
        async with aiohttp.ClientSession() as session:
            try:
                async with async_timeout.timeout(90):

                    # ── Auto-discovery BMS SN (en gång) ──────────────────────
                    if not self.device_sn:
                        try:
                            sl_result = await self._call(session, "/v1/device/storage/list", {})
                            if _is_success(sl_result):
                                device_list = (sl_result.get("data", {}) or {}).get("list", [])
                                bms = (
                                    next((d for d in device_list
                                          if str(d.get("deviceSn", "")).endswith(_BMS_SUFFIXES)), None)
                                    or (device_list[0] if device_list else None)
                                )
                                if bms:
                                    self.device_sn = bms.get("deviceSn", "")
                                    _LOGGER.info("Dyness: BMS SN hittad: %s", self.device_sn)
                                else:
                                    raise UpdateFailed(
                                        "Dyness: Inga enheter på detta API-konto. "
                                        "Kontrollera API-uppgifterna."
                                    )
                        except UpdateFailed:
                            raise
                        except Exception as e:
                            raise UpdateFailed(f"Dyness: BMS-identifiering misslyckades: {e}") from e

                    # ── Enhetsbindning (en gång) ──────────────────────────────
                    if not self._bound:
                        try:
                            bind_body = {"deviceSn": self.device_sn}
                            if self.dongle_sn:
                                bind_body["collectorSn"] = self.dongle_sn
                            bind_result = await self._call(session, "/v1/device/bindSn", bind_body)
                            bind_code = str(bind_result.get("code", ""))
                            if bind_code in ("0", "200", "500") or bind_result.get("code") in (0, 500):
                                self._bound = True
                                if bind_code == "500" or bind_result.get("code") == 500:
                                    _LOGGER.debug("Dyness bindSn: redan bunden – OK")
                                else:
                                    _LOGGER.debug("Dyness bindSn lyckades")
                            else:
                                _LOGGER.warning(
                                    "Dyness bindSn: kod %s – integrationen fortsätter ändå.",
                                    bind_code
                                )
                                self._bound = True
                        except UpdateFailed:
                            raise
                        except Exception as e:
                            _LOGGER.warning("Dyness bindSn inte tillgänglig: %s", e)
                            self._bound = True

                    # ── Statiska data (en gång) ───────────────────────────────
                    if not self.station_info:
                        try:
                            result = await self._call(
                                session, "/v1/station/info", {"deviceSn": self.device_sn}
                            )
                            if _is_success(result):
                                self.station_info = result.get("data", {}) or {}
                        except Exception as e:
                            _LOGGER.warning("Dyness station/info inte tillgänglig: %s", e)

                    # ── device_info + storage/list (var 3:e cykel) ────────────
                    # Båda hämtas i samma cykel för att hålla deviceCommunicationStatus aktuell.
                    # Utan throttling missas återanslutningar tills HA startas om.
                    self._storage_list_cycle = (self._storage_list_cycle + 1) % 3
                    if self._storage_list_cycle == 0 or not self.device_info or not self.storage_info:
                        try:
                            body = {"deviceSn": self.device_sn}
                            if self.dongle_sn:
                                body["collectorSn"] = self.dongle_sn
                            result = await self._call(
                                session, "/v1/device/household/storage/detail", body
                            )
                            if _is_success(result):
                                self.device_info = result.get("data", {}) or {}
                        except Exception as e:
                            _LOGGER.warning("Dyness household/storage/detail inte tillgänglig: %s", e)

                        try:
                            result = await self._call(session, "/v1/device/storage/list", {})
                            if _is_success(result):
                                device_list = (result.get("data", {}) or {}).get("list", [])
                                match = next(
                                    (d for d in device_list if d.get("deviceSn") == self.device_sn),
                                    device_list[0] if device_list else {}
                                )
                                self.storage_info = match
                        except Exception as e:
                            _LOGGER.warning("Dyness storage/list inte tillgänglig: %s", e)

                    # ── realTime/data BMS (vid varje uppdatering) ─────────────
                    try:
                        body = {"deviceSn": self.device_sn}
                        if self.dongle_sn:
                            body["collectorSn"] = self.dongle_sn
                        rt_result = await self._call(session, "/v1/device/realTime/data", body)
                        if _is_success(rt_result):
                            raw = rt_result.get("data", []) or []
                            self.realtime_data = {
                                item["pointId"]: item["pointValue"]
                                for item in raw
                                if isinstance(item, dict) and "pointId" in item
                            }
                            _LOGGER.debug("Dyness realTime/data: %d punkter", len(self.realtime_data))

                            # ── Sub-modul discovery via SUB-punkt ─────────────
                            sub_raw = self.realtime_data.get("SUB", "")
                            if sub_raw:
                                import re
                                candidates = [s.strip() for s in str(sub_raw).split(",") if s.strip()]
                                candidates = [
                                    s for s in candidates
                                    if not s.endswith(_BMS_SUFFIXES)
                                    and not re.search(r'-BDU-\d+$', s)
                                ]
                                if len(candidates) > 1:
                                    if set(candidates) != set(self._module_sns):
                                        _LOGGER.info(
                                            "Dyness: Sub-moduler uppdaterade: %s → %s",
                                            self._module_sns, candidates
                                        )
                                        self._module_sns = candidates
                                        self._update_scan_interval()
                                        # Uppdatera dynamisk modellbeteckning när modulantalet är känt
                                        n = len(self._module_sns)
                                        self._detected_model_label = f"STACK100-{n}S"
                                        _LOGGER.info(
                                            "Dyness: Stack100 modulantal = %d → modellbeteckning satt till '%s'",
                                            n, self._detected_model_label
                                        )
                                elif not self._module_sns:
                                    _LOGGER.debug(
                                        "Dyness: Enstaka sub-modul — inget separat anrop (%s)",
                                        candidates
                                    )
                        else:
                            _LOGGER.debug(
                                "Dyness realTime/data: kod %s – %s",
                                rt_result.get("code"), rt_result.get("info")
                            )
                    except Exception as e:
                        _LOGGER.warning("Dyness realTime/data inte tillgänglig: %s", e)

                    # ── Per-modul realTime/data ────────────────────────────────
                    new_module_data: dict[str, dict] = {}
                    for sn in self._module_sns:
                        try:
                            if sn not in self._bound_sns:
                                bind_res = await self._call(
                                    session, "/v1/device/bindSn", {"deviceSn": sn}
                                )
                                bind_code = str(bind_res.get("code", ""))
                                if bind_code in ("0", "200", "500") or bind_res.get("code") in (0, 500):
                                    self._bound_sns.add(sn)
                                    _LOGGER.info("Dyness sub-modul bunden: %s", sn)
                                else:
                                    _LOGGER.warning(
                                        "Dyness sub-modul bindning misslyckades: %s kod %s",
                                        sn, bind_code
                                    )
                                    continue
                            m_result = await self._call(
                                session, "/v1/device/realTime/data", {"deviceSn": sn}
                            )
                            if _is_success(m_result):
                                m_raw = m_result.get("data", []) or []
                                m_pts = {
                                    item["pointId"]: item["pointValue"]
                                    for item in m_raw
                                    if isinstance(item, dict) and "pointId" in item
                                }
                                mid = sn
                                new_module_data[mid] = _parse_module_points(sn, mid, m_pts)
                                _LOGGER.debug("Dyness modul %s: %d punkter", mid, len(m_pts))
                            else:
                                _LOGGER.warning("Dyness modul %s: kod %s", sn, m_result.get("code"))
                        except Exception as e:
                            _LOGGER.warning("Dyness modul %s inte tillgänglig: %s", sn, e)
                    if new_module_data:
                        self.module_data = new_module_data

                    # ── getLastRunningDataBySn (vid varje uppdatering) ─────────
                    try:
                        run_body = {"deviceSn": self.device_sn}
                        if self.dongle_sn:
                            run_body["collectorSn"] = self.dongle_sn
                        run_result = await self._call(
                            session, "/v1/device/getLastRunningDataBySn", run_body
                        )
                        if _is_success(run_result):
                            self.running_data = run_result.get("data", {}) or {}
                            all_null = all(v is None or v == "" for v in self.running_data.values())
                            if all_null:
                                _LOGGER.debug(
                                    "Dyness getLastRunningDataBySn: Alla %d fält är null "
                                    "— ingen växelriktare ansluten (normalt för ren batterienhet)",
                                    len(self.running_data)
                                )
                            else:
                                _LOGGER.debug(
                                    "Dyness getLastRunningDataBySn: %d fält, firmwareVersion=%s",
                                    len(self.running_data),
                                    self.running_data.get("firmwareVersion")
                                )
                        else:
                            _LOGGER.debug(
                                "Dyness getLastRunningDataBySn: kod %s – %s",
                                run_result.get("code"), run_result.get("info")
                            )
                    except Exception as e:
                        _LOGGER.warning("Dyness getLastRunningDataBySn inte tillgänglig: %s", e)

                    # ── Effektdata (vid varje uppdatering) ────────────────────
                    body = {"pageNo": 1, "pageSize": 1, "deviceSn": self.device_sn}
                    if self.dongle_sn:
                        body["collectorSn"] = self.dongle_sn
                    result = await self._call(
                        session, "/v1/device/getLastPowerDataBySn", body
                    )
                    code = str(result.get("code", ""))
                    _power_data_ok = code in ("0", "200") or result.get("code") == 0
                    if not _power_data_ok:
                        _LOGGER.warning(
                            "Dyness getLastPowerDataBySn misslyckades – kod %s: %s (deviceSn=%s) "
                            "— behåller senaste värden",
                            code, result.get("info"), self.device_sn,
                        )
                        # Totalfel: ingen data alls → UpdateFailed
                        if not self.realtime_data and not self.running_data:
                            raise UpdateFailed(
                                f"Dyness API-fel (kod {code}): {result.get('info', 'Okänt')} "
                                f"– deviceSn={self.device_sn}"
                            )
                        data = {}
                    else:
                        data = result.get("data", {})

                    if isinstance(data, list):
                        valid = [d for d in data if d.get("soc") is not None]
                        if not valid:
                            _LOGGER.warning(
                                "Dyness: Alla %d datapunkter har soc=null (deviceSn=%s)",
                                len(data), self.device_sn
                            )
                        data = valid[-1] if valid else (data[-1] if data else {})

                    # ── Statiska fält ─────────────────────────────────────────
                    # deviceCommunicationStatus: från storage/list (uppdateras var 3:e cykel)
                    # — tillförlitligare än device_info som kan vara föråldrad
                    comm_status = self.storage_info.get("deviceCommunicationStatus")
                    if comm_status is None:
                        comm_status = self.device_info.get("deviceCommunicationStatus")
                    data["deviceCommunicationStatus"] = comm_status
                    data["firmwareVersion"]            = self.device_info.get("firmwareVersion")
                    data["workStatus"]                 = self.storage_info.get("workStatus")

                    # ── Schema-detektering ────────────────────────────────────
                    rt = self.realtime_data
                    schema = _detect_schema(
                        self.device_info.get("deviceModelName", ""), rt
                    )
                    data["_schema"] = schema
                    _LOGGER.debug("Dyness: Schema detekterat: %s", schema)

                    # ── batteryCapacity ───────────────────────────────────────
                    # Stack100/Tower: station_info = hela klustrets kapacitet → multiplicera inte.
                    #   Stack100 korrigeras dessutom av Point 1700 i schema-blocket nedan.
                    # Junior/DL5: station_info = per-modul → multiplicera med n_modules.
                    bc_single = _to_float(self.station_info.get("batteryCapacity"))
                    n_modules = max(len(self._module_sns), 1)
                    if schema in (SCHEMA_STACK100, SCHEMA_TOWER):
                        data["batteryCapacity"] = bc_single
                    elif bc_single is not None and n_modules > 1:
                        data["batteryCapacity"] = round(bc_single * n_modules, 3)
                        _LOGGER.debug(
                            "Dyness: batteryCapacity %s × %d moduler = %s kWh",
                            bc_single, n_modules, data["batteryCapacity"]
                        )
                    else:
                        data["batteryCapacity"] = bc_single

                    # ── Schema-specifika Points ───────────────────────────────

                    if schema == SCHEMA_STACK100:
                        # Stack100 BMS — Points direkt från BMS Master
                        # Point 1100  = Totalspänning (V)
                        # Point 1500  = SOH (%)
                        # Point 1600  = Återstående kapacitet (kWh) — direkt från BMS
                        # Point 1700  = Nominell kapacitet (kWh) — direkt från BMS
                        # Point 1800  = Laddningscykler
                        # Point 1900  = Totalt laddad energi (kWh)
                        # Point 2000  = Rekommenderad max laddström (A)
                        # Point 2100  = Rekommenderad max urladdström (A)
                        # Point 2400  = Högsta cellspänning (V)
                        # Point 2500  = Modul-nr med högsta cellspänning
                        # Point 2600  = Cell-nr i modulen med högsta spänning
                        # Point 2700  = Lägsta cellspänning (V)
                        # Point 2800  = Modul-nr med lägsta cellspänning
                        # Point 2900  = Cell-nr i modulen med lägsta spänning
                        # Point 3000  = Högsta temperatur (°C)
                        # Point 3300  = Lägsta temperatur (°C)
                        # Point 4000  = Balanseringsstatus (0=inaktiv)
                        # Point 4100  = Celler per modul
                        # Point 4200  = Temperatursensorer per modul
                        # Point 4300  = Antal moduler i klustret
                        data["packVoltage"]       = rt.get("1100")
                        data["soh"]               = rt.get("1500")
                        data["cycleCount"]         = rt.get("1800")
                        data["energyChargeTotal"] = rt.get("1900")

                        # Kapacitet direkt från BMS — tillförlitligare än station_info × n_moduler,
                        # korrekt även efter modulutbyggnad (t.ex. 7 → 13 moduler).
                        stack_remaining = _to_float(rt.get("1600"))
                        stack_usable    = _to_float(rt.get("1700"))
                        if stack_remaining is not None and stack_remaining > 0:
                            data["remainingKwh"]    = stack_remaining
                        if stack_usable is not None and stack_usable > 0:
                            data["usableKwh"]       = stack_usable
                            data["batteryCapacity"] = stack_usable

                        # Cellspänningar (kluster-sammanfattning)
                        data["cellVoltageMax"]       = rt.get("2400")
                        data["cellVoltageMin"]       = rt.get("2700")
                        data["cellVoltageMaxModule"] = rt.get("2500")
                        data["cellVoltageMaxCell"]   = rt.get("2600")
                        data["cellVoltageMinModule"] = rt.get("2800")
                        data["cellVoltageMinCell"]   = rt.get("2900")

                        # Temperaturer
                        data["tempMax"] = rt.get("3000")
                        data["tempMin"] = rt.get("3300")

                        # Ström-/spänningsgränser
                        cl = _to_float(rt.get("2000"))
                        dl = _to_float(rt.get("2100"))
                        if cl is not None and cl > 0:
                            data["chargeCurrentLimit"]    = cl
                        if dl is not None and dl > 0:
                            data["dischargeCurrentLimit"] = dl

                        # Balansering (kluster-nivå)
                        bal = rt.get("4000")
                        if bal is not None:
                            data["balancingStatus"] = str(bal) != "0"

                        # Alarm-bits (5000-serien)
                        data["alarmSpreadV"] = str(rt.get("5001", "0")) == "1"
                        data["alarmSpreadT"] = str(rt.get("5002", "0")) == "1"
                        data["alarmInsul"]   = str(rt.get("5003", "0")) == "1"
                        data["alarmAfe"]     = str(rt.get("5101", "0")) == "1"
                        data["alarmBms"]     = str(rt.get("5102", "0")) == "1"
                        data["alarmSys"]     = str(rt.get("5104", "0")) == "1"
                        data["alarmTotal"]   = rt.get("9999999")

                    elif schema in (SCHEMA_JUNIOR, SCHEMA_DL5):
                        data["packVoltage"]           = rt.get("600") if rt.get("600") is not None else data.get("packVoltage")
                        data["soh"]                   = rt.get("1200")
                        data["temp"]                  = rt.get("1800")
                        data["cellVoltageMax"]         = rt.get("1300")
                        data["cellVoltageMin"]         = rt.get("1500")
                        data["energyChargeDay"]        = rt.get("7200")
                        data["energyDischargeDay"]     = rt.get("7400")
                        data["energyChargeTotal"]      = rt.get("7100")
                        data["energyDischargeTotal"]   = rt.get("7300")
                        data["tempMosfet"]             = rt.get("2300")
                        data["tempBmsMax"]             = rt.get("2800")
                        data["tempBmsMin"]             = rt.get("3000")
                        data["alarmStatus1"]           = rt.get("3200")
                        data["alarmStatus2"]           = rt.get("3300")
                        data["alarmTotal"]             = rt.get("4100")
                        if len(self._module_sns) > 0:
                            cl = _to_float(rt.get("3800"))
                            dl = _to_float(rt.get("3900"))
                            if cl is not None and cl > 0:
                                data["chargeCurrentLimit"] = cl
                            if dl is not None and dl > 0:
                                data["dischargeCurrentLimit"] = dl

                    elif schema == SCHEMA_POWERBOX_PRO:
                        # PowerBox Pro / PowerHaus — egna Points, verifierade via logg
                        # batteryCapacity från station/info = total kapacitet direkt (ej × n_moduler)
                        data["packVoltage"]          = rt.get("600") if rt.get("600") is not None else data.get("packVoltage")
                        data["soh"]                  = rt.get("1200")
                        data["cellVoltageMax"]        = rt.get("1300")
                        data["cellVoltageMin"]        = rt.get("1500")
                        data["cellVoltageMaxModule"]  = rt.get("1401")
                        data["cellVoltageMaxCell"]    = rt.get("1402")
                        data["cellVoltageMinModule"]  = rt.get("1601")
                        data["cellVoltageMinCell"]    = rt.get("1602")
                        data["tempMax"]               = rt.get("1800")
                        data["tempMin"]               = rt.get("2000")
                        data["tempMosfet"]            = rt.get("2300")
                        data["tempBmsMax"]            = rt.get("3000")
                        data["alarmStatus1"]          = rt.get("3200")
                        data["alarmStatus2"]          = rt.get("3300")
                        data["alarmTotal"]            = rt.get("4100")

                        cv = _to_float(rt.get("3600"))
                        dv = _to_float(rt.get("3700"))
                        cl = _to_float(rt.get("3800"))
                        dl = _to_float(rt.get("3900"))
                        if cv is not None and cv > 0:
                            data["chargeVoltageLimit"]    = cv
                        if dv is not None and dv > 0:
                            data["dischargeVoltageLimit"] = dv
                        if cl is not None and cl > 0:
                            data["chargeCurrentLimit"]    = cl
                        if dl is not None and dl > 0:
                            data["dischargeCurrentLimit"] = dl

                        bc  = _to_float(data.get("batteryCapacity"))
                        soc = _to_float(data.get("soc"))
                        soh = _to_float(rt.get("1200"))
                        if bc is not None and soc is not None:
                            soh_factor = (soh / 100) if (soh is not None and soh <= 100) else 1.0
                            data["usableKwh"]    = round(bc * soh_factor, 3)
                            data["remainingKwh"] = round(bc * soh_factor * soc / 100, 3)

                    elif schema == SCHEMA_TOWER:
                        data["soh"]                   = rt.get("1500")
                        data["tempMax"]               = rt.get("3000")
                        data["tempMin"]               = rt.get("3300")
                        data["cellVoltageMax"]         = rt.get("2400")
                        data["cellVoltageMin"]         = rt.get("2700")
                        data["cycleCount"]             = rt.get("1800")
                        data["energyChargeTotal"]      = rt.get("1900")
                        tower_remaining = _to_float(rt.get("1600"))
                        if tower_remaining is not None and tower_remaining > 0:
                            data["remainingKwh"] = tower_remaining
                        tower_usable = _to_float(rt.get("1700"))
                        if tower_usable is not None and tower_usable > 0:
                            data["usableKwh"] = tower_usable
                            if data.get("batteryCapacity") is None:
                                data["batteryCapacity"] = tower_usable
                        if rt.get("4400") is not None:
                            # Tower Pro TP7 alarm-schema
                            data["alarmSpreadV"] = str(rt.get("4402", "0")) == "1"
                            data["alarmSpreadT"] = str(rt.get("4403", "0")) == "1"
                            data["alarmInsul"]   = False
                            data["alarmAfe"]     = False
                            data["alarmBms"]     = False
                            data["alarmSys"]     = False
                            flags = [rt.get(str(f), "0") for f in [4400, 4500, 4600, 4700, 4800, 4900]]
                            data["alarmTotal"] = str(int(any(str(f) != "0" for f in flags)))
                        else:
                            # Tower T14 alarm-schema (verifierat)
                            data["alarmSpreadV"] = str(rt.get("5001", "0")) == "1"
                            data["alarmSpreadT"] = str(rt.get("5002", "0")) == "1"
                            data["alarmInsul"]   = str(rt.get("5003", "0")) == "1"
                            data["alarmAfe"]     = str(rt.get("5101", "0")) == "1"
                            data["alarmBms"]     = str(rt.get("5102", "0")) == "1"
                            data["alarmSys"]     = str(rt.get("5104", "0")) == "1"
                            data["alarmTotal"]   = rt.get("9999999")

                    elif schema == SCHEMA_POWERBOX_G2:
                        # PowerBox G2 (modelCode 42) — 5-siffriga Points, verifierat via logg
                        # batteryCapacity från station/info = total kapacitet direkt (ej × n_moduler)
                        data["packVoltage"] = rt.get("13500") if rt.get("13500") is not None else data.get("packVoltage")
                        data["cycleCount"]  = rt.get("13900")

                        # Temperaturer
                        bms_temp = _to_float(rt.get("12400"))
                        if bms_temp is not None:
                            data["tempBmsMax"] = bms_temp
                        cell_temps = [
                            _to_float(rt.get(str(12500 + i * 100)))
                            for i in range(4)
                        ]
                        valid_temps = [t for t in cell_temps if t is not None and t > 0]
                        if valid_temps:
                            data["tempMax"] = max(valid_temps)
                            data["tempMin"] = min(valid_temps) if len(valid_temps) > 1 else None

                        # Cellspänningar från sub-modul-data (10300–11800)
                        cells = []
                        for i in range(1, 17):
                            v = _to_float(rt.get(str(10200 + i * 100)))
                            if v is not None and v > 0:
                                cells.append(v)
                        if cells:
                            data["cellVoltageMax"] = max(cells)
                            data["cellVoltageMin"] = min(cells)

                        # Ström-/spänningsgränser
                        cv = _to_float(rt.get("18700"))
                        dv = _to_float(rt.get("18800"))
                        cl = _to_float(rt.get("18600"))
                        dl = _to_float(rt.get("19200"))
                        if cv is not None and cv > 0:
                            data["chargeVoltageLimit"]    = cv
                        if dv is not None and dv > 0:
                            data["dischargeVoltageLimit"] = dv
                        if cl is not None and cl > 0:
                            data["chargeCurrentLimit"]    = cl
                        if dl is not None and dl > 0:
                            data["dischargeCurrentLimit"] = dl

                        # Kapacitet
                        bc  = _to_float(data.get("batteryCapacity"))
                        soc = _to_float(data.get("soc"))
                        soh = _to_float(data.get("soh"))
                        if bc is not None and soc is not None:
                            soh_factor = (soh / 100) if (soh is not None and soh <= 100) else 1.0
                            data["usableKwh"]    = round(bc * soh_factor, 3)
                            data["remainingKwh"] = round(bc * soh_factor * soc / 100, 3)

                        _LOGGER.debug(
                            "Dyness PowerBox G2: packVoltage=%s V, SOC=%s%%, celler=%d, tempMax=%s°C",
                            data.get("packVoltage"), soc, len(cells), data.get("tempMax"),
                        )

                    elif schema == SCHEMA_POWERDEPOT:
                        # PowerDepot G2 (modelCode 144) — fullt verifierat
                        # Point 400 = modulantal direkt från BMS → robust mot tomma _module_sns
                        n_mod_bms = _to_float(rt.get("400"))
                        if n_mod_bms is not None and n_mod_bms > 0:
                            bc_s = _to_float(self.station_info.get("batteryCapacity"))
                            if bc_s is not None:
                                data["batteryCapacity"] = round(bc_s * int(n_mod_bms), 3)

                        data["packVoltage"]          = rt.get("600") if rt.get("600") is not None else data.get("packVoltage")
                        data["realTimeCurrent"]      = rt.get("700")
                        data["soc"]                  = rt.get("800")
                        data["soh"]                  = rt.get("1200")
                        data["cellVoltageMax"]        = rt.get("1300")
                        data["cellVoltageMaxModule"]  = rt.get("1401")
                        data["cellVoltageMaxCell"]    = rt.get("1402")
                        data["cellVoltageMin"]        = rt.get("1500")
                        data["cellVoltageMinModule"]  = rt.get("1601")
                        data["cellVoltageMinCell"]    = rt.get("1602")
                        data["tempMax"]               = rt.get("1800")
                        data["tempMaxModule"]         = rt.get("1901")
                        data["tempMin"]               = rt.get("2000")
                        data["tempMinModule"]         = rt.get("2101")
                        data["tempMosfet"]            = rt.get("2300")
                        data["tempBmsMax"]            = rt.get("2800")
                        data["tempBmsMin"]            = rt.get("3000")

                        cv = _to_float(rt.get("3600"))
                        dv = _to_float(rt.get("3700"))
                        cl = _to_float(rt.get("3800"))
                        dl = _to_float(rt.get("3900"))
                        if cv is not None and cv > 0:
                            data["chargeVoltageLimit"]    = cv
                        if dv is not None and dv > 0:
                            data["dischargeVoltageLimit"] = dv
                        if cl is not None and cl > 0:
                            data["chargeCurrentLimit"]    = cl
                        if dl is not None and dl > 0:
                            data["dischargeCurrentLimit"] = dl

                        bc     = _to_float(data.get("batteryCapacity"))
                        soc_pd = _to_float(rt.get("800"))
                        soh_pd = _to_float(rt.get("1200"))
                        if bc is not None and soc_pd is not None:
                            soh_factor = (soh_pd / 100) if (soh_pd is not None and soh_pd <= 100) else 1.0
                            data["usableKwh"]    = round(bc * soh_factor, 3)
                            data["remainingKwh"] = round(bc * soh_factor * soc_pd / 100, 3)

                        _LOGGER.debug(
                            "Dyness PowerDepot G2: n_modules=%s, batteryCapacity=%s kWh, "
                            "SOC=%s%%, packVoltage=%s V, tempMosfet=%s°C",
                            n_mod_bms, data.get("batteryCapacity"),
                            soc_pd, data.get("packVoltage"), data.get("tempMosfet"),
                        )

                    elif schema == SCHEMA_CYGNI:
                        # Cygni hybrid-växelriktare
                        # Inverterad polaritet: negativt = laddning, positivt = urladdning
                        # (omvänt mot alla andra Dyness-modeller)
                        data["packVoltage"] = rt.get("170") if rt.get("170") is not None else data.get("packVoltage")
                        data["soc"]         = rt.get("2010")
                        data["soh"]         = rt.get("2011")
                        data["temp"]        = rt.get("2003")

                        cl = _to_float(rt.get("2004"))
                        dl = _to_float(rt.get("2005"))
                        if cl is not None and cl > 0:
                            data["chargeCurrentLimit"]    = cl
                        if dl is not None and dl > 0:
                            data["dischargeCurrentLimit"] = dl

                        raw_power   = _to_float(rt.get("172"))
                        raw_current = _to_float(rt.get("171"))
                        if raw_power is not None:
                            data["realTimePower"]   = raw_power * -1
                        if raw_current is not None:
                            data["realTimeCurrent"] = raw_current * -1

                        bc    = _to_float(data.get("batteryCapacity"))
                        soc_c = _to_float(rt.get("2010"))
                        soh_c = _to_float(rt.get("2011"))
                        if bc is not None and soc_c is not None:
                            soh_factor = (soh_c / 100) if (soh_c is not None and soh_c <= 100) else 1.0
                            data["usableKwh"]    = round(bc * soh_factor, 3)
                            data["remainingKwh"] = round(bc * soh_factor * soc_c / 100, 3)

                        _LOGGER.debug(
                            "Dyness Cygni: packVoltage=%s V, SOC=%s%%, SOH=%s%%, "
                            "power=%s W (polaritet korrigerad), temp=%s°C",
                            data.get("packVoltage"), soc_c, soh_c,
                            data.get("realTimePower"), data.get("temp"),
                        )

                    # ── Temperatur-logik ──────────────────────────────────────
                    # Om tempMax == tempMin → ta bort tempMin (undviker dubbelsenor)
                    t_max = _to_float(data.get("tempMax"))
                    t_min = _to_float(data.get("tempMin"))
                    if t_max is not None and t_min is not None and t_max == t_min:
                        data.pop("tempMin", None)

                    bms_max = _to_float(data.get("tempBmsMax"))
                    bms_min = _to_float(data.get("tempBmsMin"))
                    if bms_max is not None and bms_min is not None and bms_max == bms_min:
                        data.pop("tempBmsMin", None)

                    # ── Beräknade fält ────────────────────────────────────────
                    try:
                        vmax = _to_float(data.get("cellVoltageMax"))
                        vmin = _to_float(data.get("cellVoltageMin"))
                        if vmax is not None and vmin is not None and vmax > 0 and vmin > 0:
                            data["cellVoltageDiffMv"] = round((vmax - vmin) * 1000, 1)
                    except (ValueError, TypeError):
                        pass

                    try:
                        power = float(data.get("realTimePower") or 0)
                        data["batteryStatus"] = (
                            "Charging"    if power >  10 else
                            "Discharging" if power < -10 else
                            "Standby"
                        )
                    except (ValueError, TypeError):
                        pass

                    # ── getLastRunningDataBySn-fält ───────────────────────────
                    rd = self.running_data
                    if rd:
                        _GRID_STATUS = {"0": "Off Grid", "1": "On Grid"}
                        _RUN_MODE    = {"0": "Self-use", "1": "Feed-in Priority", "2": "Backup", "3": "Manual"}

                        for key, rdkey in [
                            ("pvPower",   "pvPower"),
                            ("loadPower", "loadPower"),
                            ("gridPower", "activePower"),
                            ("pv1Power",  "pv1Power"),
                            ("pv2Power",  "pv2Power"),
                            ("pv3Power",  "pv3Power"),
                            ("pv4Power",  "pv4Power"),
                        ]:
                            v = _to_float(rd.get(rdkey))
                            if v is not None:
                                data[key] = v

                        for key, rdkey in [
                            ("pvEnergyToday",   "dayGeneration"),
                            ("loadEnergyToday", "dayElectricity"),
                            ("gridImportToday", "buyEnergy"),
                            ("gridExportToday", "sellEnergy"),
                            ("pvEnergyTotal",   "totalGeneration"),
                            ("loadEnergyTotal", "totalElectricity"),
                            ("gridImportTotal", "totalBuyEnergy"),
                            ("gridExportTotal", "totalSellEnergy"),
                        ]:
                            v = _to_float(rd.get(rdkey))
                            if v is not None:
                                data[key] = v

                        for key, rdkey in [
                            ("tempInternal", "internalTemperature"),
                            ("tempModule",   "moduleTemperature"),
                            ("tempHeatSink", "heatDissipationTemperature"),
                        ]:
                            v = _to_float(rd.get(rdkey))
                            if v is not None:
                                data[key] = v

                        data["gridStatus"]         = _GRID_STATUS.get(str(rd.get("gridStatus", "")), rd.get("gridStatus"))
                        data["runModel"]           = _RUN_MODE.get(str(rd.get("runModel", "")), rd.get("runModel"))
                        data["inverterWorkStatus"] = rd.get("workStatus")

                        for key, rdkey in [
                            ("gridVoltage",   "rvoltage"),
                            ("gridCurrent",   "rcurrent"),
                            ("gridFrequency", "gridFrequencyR"),
                            ("busVoltage",    "busVoltage"),
                            ("pv1Voltage",    "pv1Voltage"),
                            ("pv2Voltage",    "pv2Voltage"),
                            ("pv3Voltage",    "pv3Voltage"),
                            ("pv1Current",    "pv1Current"),
                            ("pv2Current",    "pv2Current"),
                            ("pv3Current",    "pv3Current"),
                        ]:
                            v = _to_float(rd.get(rdkey))
                            if v is not None:
                                data[key] = v

                        cl = _to_float(rd.get("chargingLimitCurrent"))
                        dl = _to_float(rd.get("dischargeLimitCurrent"))
                        if cl is not None and cl > 0:
                            data["chargeCurrentLimit"]    = cl
                        if dl is not None and dl > 0:
                            data["dischargeCurrentLimit"] = dl

                        if data.get("soc") is None:
                            soc_rd = rd.get("batterySoc")
                            if soc_rd is not None:
                                data["soc"] = str(soc_rd)
                        if data.get("realTimePower") is None:
                            bp = _to_float(rd.get("batteryPower"))
                            if bp is not None:
                                data["realTimePower"] = bp

                    # ── Alarm-textdekodning ───────────────────────────────────
                    # Junior/DL5/PowerBox Pro: 3200/3300-serien
                    _ALARM_BITS_JUNIOR = {
                        "3201": "Cell voltage consistency warning",
                        "3202": "MOSFET high temperature",
                        "3203": "Cell low temperature",
                        "3204": "Cell high temperature",
                        "3205": "Cell low voltage",
                        "3206": "Cell high voltage",
                        "3207": "Pack low voltage",
                        "3208": "Pack high voltage",
                        "3305": "Internal communication error",
                        "3306": "Discharge overcurrent",
                        "3307": "Charge overcurrent",
                        "3308": "Cell temperature consistency warning",
                    }
                    # Stack100/Tower: 5000-serien (utökad med 5004-5008, 5103)
                    _ALARM_BITS_5000 = {
                        "5001": "Voltage spread alarm",
                        "5002": "Temperature spread alarm",
                        "5003": "Low insulation alarm",
                        "5004": "Invalid cell voltage",
                        "5005": "Invalid temperature",
                        "5006": "Invalid current",
                        "5007": "Relay open fault",
                        "5008": "Relay close fault",
                        "5101": "AFE communication error",
                        "5102": "BMS communication error",
                        "5103": "PCS communication error",
                        "5104": "System fault",
                    }
                    # Stack100/Tower TP7: 4400-serien (flag registers L1/L2)
                    _FLAG_LABELS = {
                        "4401": "Pack voltage high L1",    "4402": "Cell voltage high L1",
                        "4403": "Charge temp high L1",     "4404": "Charge temp low L1",
                        "4405": "Charge current high L1",
                        "4501": "Pack voltage high L2",    "4502": "Cell voltage high L2",
                        "4503": "Charge temp high L2",     "4504": "Charge temp low L2",
                        "4505": "Charge current high L2",
                        "4701": "Pack voltage low L1",     "4702": "Cell voltage low L1",
                        "4703": "Discharge temp high L1",  "4704": "Discharge temp low L1",
                        "4705": "Discharge current high L1",
                        "4801": "Pack voltage low L2",     "4802": "Cell voltage low L2",
                        "4803": "Discharge temp high L2",  "4804": "Discharge temp low L2",
                        "4805": "Discharge current high L2",
                    }
                    alarm_texts = []
                    if schema in (SCHEMA_JUNIOR, SCHEMA_DL5, SCHEMA_POWERBOX_PRO):
                        for pid, label in _ALARM_BITS_JUNIOR.items():
                            if str(rt.get(pid, "0")) == "1":
                                alarm_texts.append(label)
                    if schema in (SCHEMA_STACK100, SCHEMA_TOWER):
                        for pid, label in _ALARM_BITS_5000.items():
                            if str(rt.get(pid, "0")) == "1":
                                alarm_texts.append(label)
                        for pid, label in _FLAG_LABELS.items():
                            if str(rt.get(pid, "0")) == "1":
                                alarm_texts.append(label)

                    if alarm_texts:
                        data["alarmText"] = ", ".join(alarm_texts)
                        self.hass.async_create_task(
                            self.hass.services.async_call(
                                "persistent_notification", "create", {
                                    "title": "⚠️ Dyness Battery Alarm",
                                    "message": (
                                        f"Active alarms detected on {self.device_sn}:\n"
                                        + "\n".join(f"• {t}" for t in alarm_texts)
                                        + "\n\nPlease contact Dyness support if the issue persists."
                                    ),
                                    "notification_id": f"dyness_alarm_{self.device_sn}",
                                }
                            )
                        )
                    else:
                        data["alarmText"] = "OK"
                        self.hass.async_create_task(
                            self.hass.services.async_call(
                                "persistent_notification", "dismiss", {
                                    "notification_id": f"dyness_alarm_{self.device_sn}",
                                }
                            )
                        )

                    # ── stationName ───────────────────────────────────────────
                    data["stationName"] = (
                        self.device_info.get("stationName")
                        or self.storage_info.get("stationName")
                        or "Dyness Battery"
                    )

                    # ── Spänningsgränser (Junior/DL5 only) ───────────────────
                    if schema in (SCHEMA_JUNIOR, SCHEMA_DL5):
                        cv = _to_float(rt.get("3600"))
                        dv = _to_float(rt.get("3700"))
                        if cv is not None and cv > 0:
                            data["chargeVoltageLimit"] = cv
                        if dv is not None and dv > 0:
                            data["dischargeVoltageLimit"] = dv

                    # ── Balansering (Junior/DL5 only) ─────────────────────────
                    if schema in (SCHEMA_JUNIOR, SCHEMA_DL5):
                        bal = rt.get("4000")
                        if bal is not None:
                            data["balancingStatus"] = str(bal) != "0"

                    # ── Moduldata ─────────────────────────────────────────────
                    data["module_data"] = self.module_data
                    data["moduleCount"] = len(self._module_sns)

                    # ── Dynamisk modellbeteckning (Stack100) ──────────────────
                    # Exponeras så att sensor.py kan använda den i device_info.
                    data["_detected_model_label"] = self._detected_model_label

                    # ── usableKwh / remainingKwh fallback ────────────────────
                    # Stack100/Tower/PowerBox Pro/G2/PowerDepot/Cygni sätter dessa
                    # direkt i schema-blocket ovan. Fallback körs bara om de saknas.
                    if data.get("usableKwh") is None or data.get("remainingKwh") is None:
                        try:
                            mod_data = data.get("module_data", {})
                            total_remain_kwh = 0.0
                            total_usable_kwh = 0.0
                            valid_modules    = 0
                            for mod in mod_data.values():
                                remain_ah = _to_float(mod.get("remain_ah"))
                                total_ah  = _to_float(mod.get("total_ah"))
                                voltage   = _to_float(mod.get("voltage"))
                                if (remain_ah is not None and total_ah is not None
                                        and voltage is not None
                                        and total_ah > 0 and voltage > 10):
                                    total_remain_kwh += remain_ah * voltage / 1000
                                    total_usable_kwh += total_ah  * voltage / 1000
                                    valid_modules    += 1
                            if valid_modules > 0 and total_usable_kwh > 0:
                                data["usableKwh"]    = round(total_usable_kwh, 3)
                                data["remainingKwh"] = round(total_remain_kwh, 3)
                                _LOGGER.debug(
                                    "Dyness: usableKwh=%.3f remainingKwh=%.3f (från %d moduler via Ah)",
                                    total_usable_kwh, total_remain_kwh, valid_modules,
                                )
                            elif data.get("usableKwh") is None:
                                bc  = _to_float(data.get("batteryCapacity"))
                                soc = _to_float(data.get("soc"))
                                soh = _to_float(data.get("soh"))
                                if bc is not None and soc is not None and soh is not None and soh <= 100:
                                    usable    = round(bc * (soh / 100), 3)
                                    remaining = round(usable * (soc / 100), 3)
                                    data["usableKwh"]    = usable
                                    data["remainingKwh"] = remaining
                                    _LOGGER.debug(
                                        "Dyness: usableKwh=%.3f remainingKwh=%.3f "
                                        "(SOC-fallback: bc=%.3f × soh=%.1f%% × soc=%.1f%%)",
                                        usable, remaining, bc, soh, soc,
                                    )
                        except (ValueError, TypeError):
                            pass

                    return data

            except UpdateFailed:
                raise
            except asyncio.TimeoutError as err:
                _LOGGER.warning("Dyness API timeout – försöker igen vid nästa uppdatering")
                raise UpdateFailed("Dyness API timeout") from err
            except aiohttp.ClientError as err:
                _LOGGER.error("Dyness anslutningsfel: %s", err)
                raise UpdateFailed(f"Anslutningsfel till Dyness API: {err}") from err
            except Exception as err:
                _LOGGER.error("Dyness oväntat fel: %s", err, exc_info=True)
                raise UpdateFailed(f"Oväntat fel: {err}") from err


def _parse_module_points(sn: str, mid: str, pts: dict) -> dict:
    """Parsar sub-modul datapunkter med dynamisk cell- och temperaturdetektering.

    Antal celler läses från Point 11100, antal temperatursensorer från Point 14200.
    Celler/sensorer med värde 0.0 eller None (ej monterade) registreras aldrig.

    Schema-detektering:
    - Stack100:  Point 10010 (PACK_SN) + 11000 (modulnummer) → normalt 16 celler
    - Tower TP7: Stack100-schema + Point 11100 = 30 → 30 celler
    - Tower T14: Point 10000 saknas, 11200 finns → alltid 30 celler
    - DL5.0C:    Point 10000 finns, 10010 saknas, 10300 finns → 16 celler
    """
    def g(key): return pts.get(key) if pts.get(key) not in (None, "") else None

    d = {"sn": sn, "module_id": mid}
    has_module_sn = pts.get("10000") is not None
    is_stack100   = pts.get("10010") is not None and pts.get("11000") is not None
    is_tower      = not has_module_sn and not is_stack100 and pts.get("11200") is not None
    is_dl5        = has_module_sn and not is_stack100 and pts.get("10300") is not None

    cell_count_pt = _to_float(pts.get("11100")) if is_stack100 else None
    is_tp7_module = is_stack100 and cell_count_pt is not None and int(cell_count_pt) == 30

    if is_stack100 and not is_tp7_module:
        # Stack100: läs cellantal dynamiskt från Point 11100 (normalt 16)
        cell_count = int(cell_count_pt) if cell_count_pt is not None else 16
        cells = []
        for i in range(1, cell_count + 1):
            pid = str(11100 + i * 100)
            v = _to_float(pts.get(pid))
            if v is not None and v > 0:
                d[f"cell_{i:02d}"] = v
                cells.append(v)
            # v == 0.0 eller None → cell ej monterad → registreras inte

        # Temperatursensorer: läs antal från Point 14200
        temp_count = int(_to_float(pts.get("14200")) or 0)
        temps_valid = []
        for i in range(temp_count):
            t = _to_float(pts.get(str(14300 + i * 100)))
            if t is not None and t > 0:
                temps_valid.append(t)
        if temps_valid:
            d["cell_temp_1"] = temps_valid[0]
            if len(temps_valid) > 1:
                d["cell_temp_2"] = temps_valid[1]

        d["module_number"] = _to_float(pts.get("11000"))
        _LOGGER.debug(
            "Dyness modul %s: Stack100, %d celler, %d temperatursensorer",
            mid, len(cells), len(temps_valid)
        )

    elif is_tp7_module:
        # Tower Pro TP7: dynamiskt cellantal från Point 11100 (normalt 30)
        d["is_tp7"] = True
        cell_count = int(cell_count_pt)
        cells = []
        for i in range(1, cell_count + 1):
            pid = str(11100 + i * 100)
            v = _to_float(pts.get(pid))
            if v is not None and v > 0:
                d[f"cell_{i:02d}"] = v
                cells.append(v)

        temp_count = int(_to_float(pts.get("14200")) or 0)
        temps_valid = []
        for i in range(temp_count):
            t = _to_float(pts.get(str(14300 + i * 100)))
            if t is not None and t > 0:
                temps_valid.append(t)
        if temps_valid:
            d["cell_temp_1"] = temps_valid[0]
            if len(temps_valid) > 1:
                d["cell_temp_2"] = temps_valid[1]

        d["module_number"] = _to_float(pts.get("11000"))

    elif is_tower:
        # Tower T14: alltid 30 celler
        d["cell_temp_1"] = _to_float(g("14300"))
        d["cell_temp_2"] = _to_float(g("14400"))
        cells = []
        for i in range(1, 31):
            pid = str(11100 + i * 100)
            v = _to_float(pts.get(pid))
            if v is not None and v > 0:
                d[f"cell_{i:02d}"] = v
                cells.append(v)

    elif is_dl5:
        # DL5.0C / PowerBox Pro: 16 celler
        soc_raw = _to_float(g("14000"))
        soh_raw = _to_float(g("14100"))
        if soc_raw is not None and soc_raw <= 100:
            d["soc"] = soc_raw
        if soh_raw is not None and soh_raw <= 100:
            d["soh"] = soh_raw
        cap14000 = _to_float(g("14000"))
        cap14100 = _to_float(g("14100"))
        if cap14000 is not None and cap14000 > 100:
            d["remain_ah"] = cap14000
            d["total_ah"]  = cap14100 if cap14100 is not None else None
        else:
            d["remain_ah"] = _to_float(g("13600"))
            d["total_ah"]  = _to_float(g("13800"))
        d["cycle_count"] = _to_float(g("13900"))
        d["bms_temp"]    = _to_float(g("12400"))
        d["cell_temp_1"] = _to_float(g("12500"))
        d["cell_temp_2"] = _to_float(g("12600"))
        d["voltage"]     = _to_float(g("13500"))
        d["current"]     = _to_float(g("13400"))
        cells = []
        for i in range(1, 17):
            pid = str(10200 + i * 100)
            v = _to_float(pts.get(pid))
            if v is not None and v > 0:
                d[f"cell_{i:02d}"] = v
                cells.append(v)
        alarm = any(int(pts.get(str(14300 + i * 100)) or 0) != 0 for i in range(16))
        d["has_alarm"] = alarm
    else:
        cells = []

    if cells:
        d["cell_voltage_max"]       = max(cells)
        d["cell_voltage_min"]       = min(cells)
        d["cell_voltage_spread_mv"] = round((max(cells) - min(cells)) * 1000, 1)

    return d
