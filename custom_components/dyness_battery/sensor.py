"""Sensorer för Dyness Battery Integration."""
import logging
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    PERCENTAGE, UnitOfPower, UnitOfElectricCurrent, UnitOfEnergy,
    UnitOfTemperature, UnitOfElectricPotential, UnitOfFrequency,
)
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)

# (key, translation_key, unit, device_class, state_class, icon, precision, entity_category)
_D = EntityCategory.DIAGNOSTIC

SENSORS = [
    # ── Haupt-Sensoren ────────────────────────────────────────────────────────
    ("soc",                    "battery_soc",            PERCENTAGE,                   SensorDeviceClass.BATTERY,     SensorStateClass.MEASUREMENT,      "mdi:battery-high",           None, None),
    ("realTimePower",          "battery_power",          UnitOfPower.WATT,             SensorDeviceClass.POWER,       SensorStateClass.MEASUREMENT,      "mdi:lightning-bolt",         None, None),
    ("realTimeCurrent",        "battery_current",        UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT,     SensorStateClass.MEASUREMENT,      "mdi:current-dc",             None, None),
    ("batteryStatus",          "battery_status",         None,                         None,                          None,                              "mdi:battery-charging",       None, None),
    ("packVoltage",            "pack_voltage",           UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE,     SensorStateClass.MEASUREMENT,      "mdi:sine-wave",              3,    None),
    ("soh",                    "battery_soh",            PERCENTAGE,                   SensorDeviceClass.BATTERY,     SensorStateClass.MEASUREMENT,      "mdi:battery-heart",          None, None),
    ("temp",                   "temperature",            UnitOfTemperature.CELSIUS,    SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT,      "mdi:thermometer",            None, None),
    ("tempMax",                "temp_max",               UnitOfTemperature.CELSIUS,    SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT,      "mdi:thermometer-high",       None, None),
    ("tempMin",                "temp_min",               UnitOfTemperature.CELSIUS,    SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT,      "mdi:thermometer-low",        None, None),
    ("cellVoltageMax",         "cell_voltage_max",       UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE,     SensorStateClass.MEASUREMENT,      "mdi:sine-wave",              3,    None),
    ("cellVoltageMin",         "cell_voltage_min",       UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE,     SensorStateClass.MEASUREMENT,      "mdi:sine-wave",              3,    None),
    ("cellVoltageDiffMv",      "cell_voltage_diff_mv",   "mV",                         None,                          SensorStateClass.MEASUREMENT,      "mdi:arrow-expand-horizontal", 1,   None),
    ("energyChargeDay",        "energy_charge_day",      UnitOfEnergy.KILO_WATT_HOUR,  SensorDeviceClass.ENERGY,      SensorStateClass.TOTAL_INCREASING, "mdi:battery-charging",       None, None),
    ("energyDischargeDay",     "energy_discharge_day",   UnitOfEnergy.KILO_WATT_HOUR,  SensorDeviceClass.ENERGY,      SensorStateClass.TOTAL_INCREASING, "mdi:battery-minus",          None, None),
    ("energyChargeTotal",      "energy_charge_total",    UnitOfEnergy.KILO_WATT_HOUR,  SensorDeviceClass.ENERGY,      SensorStateClass.TOTAL_INCREASING, "mdi:battery-charging-100",   None, None),
    ("energyDischargeTotal",   "energy_discharge_total", UnitOfEnergy.KILO_WATT_HOUR,  SensorDeviceClass.ENERGY,      SensorStateClass.TOTAL_INCREASING, "mdi:battery-minus-outline",  None, None),
    ("cycleCount",             "cycle_count",            None,                         None,                          SensorStateClass.TOTAL_INCREASING, "mdi:battery-sync",           None, None),
    ("usableKwh",              "usable_kwh",             UnitOfEnergy.KILO_WATT_HOUR,  SensorDeviceClass.ENERGY,      None,                              "mdi:battery-heart",          None, None),
    ("remainingKwh",           "remaining_kwh",          UnitOfEnergy.KILO_WATT_HOUR,  SensorDeviceClass.ENERGY,      None,                              "mdi:battery-charging",       None, None),
    # Max Lade-/Entladestrom (Diagnostic)
    ("chargeCurrentLimit",     "charge_current_limit",   UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT,     SensorStateClass.MEASUREMENT,      "mdi:current-ac",             None, _D),
    ("dischargeCurrentLimit",  "discharge_current_limit",UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT,     SensorStateClass.MEASUREMENT,      "mdi:current-ac",             None, _D),
    # Alarm Text + neue Sensoren
    ("alarmText",              "alarm_text",             None,                         None,                          None,                              "mdi:alert-circle-outline",   None, _D),
    ("chargeVoltageLimit",     "charge_voltage_limit",   UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE,     SensorStateClass.MEASUREMENT,      "mdi:battery-arrow-up",       1,    _D),
    ("dischargeVoltageLimit",  "discharge_voltage_limit",UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE,     SensorStateClass.MEASUREMENT,      "mdi:battery-arrow-down",     1,    _D),
    ("cellVoltageMaxModule",   "cell_v_max_module",      None,                         None,                          None,                              "mdi:numeric",                None, _D),
    ("cellVoltageMaxCell",     "cell_v_max_cell",        None,                         None,                          None,                              "mdi:numeric",                None, _D),
    ("cellVoltageMinModule",   "cell_v_min_module",      None,                         None,                          None,                              "mdi:numeric",                None, _D),
    ("cellVoltageMinCell",     "cell_v_min_cell",        None,                         None,                          None,                              "mdi:numeric",                None, _D),
    ("balancingStatus",        "balancing_status",       None,                         None,                          None,                              "mdi:scale-balance",          None, _D),
    # Inverter / Hybrid Sensoren (aus getLastRunningDataBySn — nur wenn verfügbar)
    ("pvPower",            "pv_power",            UnitOfPower.WATT,               SensorDeviceClass.POWER,       SensorStateClass.MEASUREMENT,      "mdi:solar-power",            None, None),
    ("loadPower",          "load_power",           UnitOfPower.WATT,               SensorDeviceClass.POWER,       SensorStateClass.MEASUREMENT,      "mdi:home-lightning-bolt",    None, None),
    ("gridPower",          "grid_power",           UnitOfPower.WATT,               SensorDeviceClass.POWER,       SensorStateClass.MEASUREMENT,      "mdi:transmission-tower",     None, None),
    ("pv1Power",           "pv1_power",            UnitOfPower.WATT,               SensorDeviceClass.POWER,       SensorStateClass.MEASUREMENT,      "mdi:solar-panel",            None, None),
    ("pv2Power",           "pv2_power",            UnitOfPower.WATT,               SensorDeviceClass.POWER,       SensorStateClass.MEASUREMENT,      "mdi:solar-panel",            None, None),
    ("pv3Power",           "pv3_power",            UnitOfPower.WATT,               SensorDeviceClass.POWER,       SensorStateClass.MEASUREMENT,      "mdi:solar-panel",            None, None),
    ("pvEnergyToday",      "pv_energy_today",      UnitOfEnergy.KILO_WATT_HOUR,    SensorDeviceClass.ENERGY,      SensorStateClass.TOTAL_INCREASING, "mdi:solar-power",            None, None),
    ("loadEnergyToday",    "load_energy_today",    UnitOfEnergy.KILO_WATT_HOUR,    SensorDeviceClass.ENERGY,      SensorStateClass.TOTAL_INCREASING, "mdi:home-lightning-bolt",    None, None),
    ("gridImportToday",    "grid_import_today",    UnitOfEnergy.KILO_WATT_HOUR,    SensorDeviceClass.ENERGY,      SensorStateClass.TOTAL_INCREASING, "mdi:transmission-tower",     None, None),
    ("gridExportToday",    "grid_export_today",    UnitOfEnergy.KILO_WATT_HOUR,    SensorDeviceClass.ENERGY,      SensorStateClass.TOTAL_INCREASING, "mdi:transmission-tower",     None, None),
    ("pvEnergyTotal",      "pv_energy_total",      UnitOfEnergy.KILO_WATT_HOUR,    SensorDeviceClass.ENERGY,      SensorStateClass.TOTAL_INCREASING, "mdi:solar-power",            None, None),
    ("loadEnergyTotal",    "load_energy_total",    UnitOfEnergy.KILO_WATT_HOUR,    SensorDeviceClass.ENERGY,      SensorStateClass.TOTAL_INCREASING, "mdi:home-lightning-bolt",    None, None),
    ("gridImportTotal",    "grid_import_total",    UnitOfEnergy.KILO_WATT_HOUR,    SensorDeviceClass.ENERGY,      SensorStateClass.TOTAL_INCREASING, "mdi:transmission-tower",     None, None),
    ("gridExportTotal",    "grid_export_total",    UnitOfEnergy.KILO_WATT_HOUR,    SensorDeviceClass.ENERGY,      SensorStateClass.TOTAL_INCREASING, "mdi:transmission-tower",     None, None),
    ("tempInternal",       "temp_internal",        UnitOfTemperature.CELSIUS,      SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT,      "mdi:thermometer",            None, _D),
    ("tempModule",         "temp_module",          UnitOfTemperature.CELSIUS,      SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT,      "mdi:thermometer",            None, _D),
    ("tempHeatSink",       "temp_heat_sink",       UnitOfTemperature.CELSIUS,      SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT,      "mdi:thermometer",            None, _D),
    ("gridStatus",         "grid_status",          None,                           None,                          None,                              "mdi:transmission-tower",     None, None),
    ("runModel",           "run_model",            None,                           None,                          None,                              "mdi:cog",                    None, None),
    ("inverterWorkStatus", "inverter_work_status", None,                           None,                          None,                              "mdi:home-battery",           None, _D),
    ("gridVoltage",        "grid_voltage",         UnitOfElectricPotential.VOLT,   SensorDeviceClass.VOLTAGE,     SensorStateClass.MEASUREMENT,      "mdi:sine-wave",              1,    _D),
    ("gridCurrent",        "grid_current",         UnitOfElectricCurrent.AMPERE,   SensorDeviceClass.CURRENT,     SensorStateClass.MEASUREMENT,      "mdi:current-ac",             1,    _D),
    ("gridFrequency",      "grid_frequency",       UnitOfFrequency.HERTZ,          SensorDeviceClass.FREQUENCY,   SensorStateClass.MEASUREMENT,      "mdi:sine-wave",              2,    _D),
    ("busVoltage",         "bus_voltage",          UnitOfElectricPotential.VOLT,   SensorDeviceClass.VOLTAGE,     SensorStateClass.MEASUREMENT,      "mdi:sine-wave",              1,    _D),
    ("pv1Voltage",         "pv1_voltage",          UnitOfElectricPotential.VOLT,   SensorDeviceClass.VOLTAGE,     SensorStateClass.MEASUREMENT,      "mdi:solar-panel",            1,    _D),
    ("pv2Voltage",         "pv2_voltage",          UnitOfElectricPotential.VOLT,   SensorDeviceClass.VOLTAGE,     SensorStateClass.MEASUREMENT,      "mdi:solar-panel",            1,    _D),
    ("pv3Voltage",         "pv3_voltage",          UnitOfElectricPotential.VOLT,   SensorDeviceClass.VOLTAGE,     SensorStateClass.MEASUREMENT,      "mdi:solar-panel",            1,    _D),
    ("pv1Current",         "pv1_current",          UnitOfElectricCurrent.AMPERE,   SensorDeviceClass.CURRENT,     SensorStateClass.MEASUREMENT,      "mdi:solar-panel",            1,    _D),
    ("pv2Current",         "pv2_current",          UnitOfElectricCurrent.AMPERE,   SensorDeviceClass.CURRENT,     SensorStateClass.MEASUREMENT,      "mdi:solar-panel",            1,    _D),
    ("pv3Current",         "pv3_current",          UnitOfElectricCurrent.AMPERE,   SensorDeviceClass.CURRENT,     SensorStateClass.MEASUREMENT,      "mdi:solar-panel",            1,    _D),
    # Tower Alarm-Bits (Boolean, Diagnostic) — nur einmal registriert
    ("alarmSpreadV",           "alarm_spread_v",         None,                         None,                          None,                              "mdi:alert-circle-outline",   None, _D),
    ("alarmSpreadT",           "alarm_spread_t",         None,                         None,                          None,                              "mdi:alert-circle-outline",   None, _D),
    ("alarmInsul",             "alarm_insul",            None,                         None,                          None,                              "mdi:shield-alert",           None, _D),
    ("alarmAfe",               "alarm_afe",              None,                         None,                          None,                              "mdi:lan-disconnect",         None, _D),
    ("alarmBms",               "alarm_bms",              None,                         None,                          None,                              "mdi:lan-disconnect",         None, _D),
    ("alarmSys",               "alarm_sys",              None,                         None,                          None,                              "mdi:alert",                  None, _D),
    ("alarmTotal",             "alarm_total",            None,                         None,                          None,                              "mdi:alert",                  None, _D),
    # JuniorBox / DL5.0C Temperaturen (nur wenn vorhanden)
    ("tempMosfet",             "temp_mosfet",            UnitOfTemperature.CELSIUS,    SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT,      "mdi:thermometer",            None, None),
    ("tempBmsMax",             "temp_bms_max",           UnitOfTemperature.CELSIUS,    SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT,      "mdi:thermometer",            None, None),
    ("tempBmsMin",             "temp_bms_min",           UnitOfTemperature.CELSIUS,    SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT,      "mdi:thermometer",            None, None),
    # ── Diagnose ─────────────────────────────────────────────────────────────
    ("createTime",             "last_update",            None,                         None,                          None,                              "mdi:clock-outline",          None, _D),
    ("batteryCapacity",        "battery_capacity",       UnitOfEnergy.KILO_WATT_HOUR,  SensorDeviceClass.ENERGY,      None,                              "mdi:battery",                None, _D),
    ("deviceCommunicationStatus", "communication_status", None,                        None,                          None,                              "mdi:wifi",                   None, _D),
    ("firmwareVersion",        "firmware_version",       None,                         None,                          None,                              "mdi:chip",                   None, _D),
    ("workStatus",             "work_status",            None,                         None,                          None,                              "mdi:home-battery",           None, _D),
]

ALWAYS_REGISTER = {
    "soc", "realTimePower", "realTimeCurrent", "createTime",
    "batteryCapacity", "deviceCommunicationStatus", "firmwareVersion",
    "workStatus", "batteryStatus",
}


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    available_data = coordinator.data or {}

    # Pack-Level Sensoren
    async_add_entities([
        DynessSensor(coordinator, entry, key, translation_key,
                     unit, device_class, state_class, icon, precision, entity_category)
        for key, translation_key, unit, device_class, state_class, icon, precision, entity_category in SENSORS
        if key in ALWAYS_REGISTER or available_data.get(key) is not None
    ])

    # ── Modul-Sensoren ────────────────────────────────────────────────────────
    from homeassistant.helpers import entity_registry as er

    # Sensors only available at the BMS master level on Stack100 — never per-module.
    # Registering them per-module would produce permanently Unavailable entities.
    _STACK100_MODULE_SKIP = {
        'soc', 'soh', 'voltage', 'current', 'cycle_count', 'bms_temp', 'has_alarm',
    }

    # Same for Tower Pro TP7 sub-modules (SOC/SOH/voltage etc. only on master).
    _TP7_MODULE_SKIP = {
        'soc', 'soh', 'voltage', 'current', 'cycle_count', 'bms_temp', 'has_alarm',
    }

    def _is_stack100_module(mod: dict) -> bool:
        """Detect a Stack100 sub-module.

        Stack100 modules carry cell voltages and temperatures but NOT soc/voltage/current
        — those live only on the BMS master. The module_number point (11000) is always
        present on Stack100 sub-modules.
        """
        return (
            not mod.get("is_tp7")
            and mod.get("soc") is None
            and mod.get("voltage") is None
            and mod.get("module_number") is not None
        )

    # known_module_ids: prevents re-adding the same module within one session.
    # Intentionally left empty at startup — HA re-uses existing registry entries via
    # unique_id automatically, so re-calling async_add_entities after a restart is safe
    # and necessary to re-attach the coordinator. True duplicates within a session are
    # prevented by this set from the second _add_new_modules call onward.
    known_module_ids: set = set()
    _registry_scanned: bool = False

    def _add_new_modules() -> None:
        nonlocal _registry_scanned
        module_data = (coordinator.data or {}).get("module_data", {})
        if not module_data:
            return

        # On the very first call: log how many modules the registry already knows about
        # (purely informational — we do NOT pre-populate known_module_ids from registry
        # because all modules must be instaniated to re-attach the coordinator on restart).
        if not _registry_scanned:
            _registry_scanned = True
            _er = er.async_get(hass)
            registry_mids = {
                parts[1]
                for entity in er.async_entries_for_config_entry(_er, entry.entry_id)
                if len(parts := entity.unique_id.split("_")) >= 3
                and len(parts[1]) >= 8 and parts[1].isalnum()
            }
            _LOGGER.debug(
                "Dyness: Registry-Scan: %d Modul(e) bereits bekannt: %s",
                len(registry_mids), registry_mids or "leer (Neuinstallation)"
            )
            # known_module_ids intentionally left empty — see comment above.

        new_mids = [mid for mid in module_data if mid not in known_module_ids]
        if not new_mids:
            return

        new_entities = []
        for mid in new_mids:
            known_module_ids.add(mid)
            mod = module_data[mid]
            is_tp7_mod      = bool(mod.get("is_tp7"))
            is_stack100_mod = _is_stack100_module(mod)

            for data_key, trans_key, unit, dev_cls, state_cls, icon, precision in MODULE_SENSORS:
                # Skip sensors that are only available on the BMS master, not per-module
                if is_tp7_mod and data_key in _TP7_MODULE_SKIP:
                    continue
                if is_stack100_mod and data_key in _STACK100_MODULE_SKIP:
                    continue
                # For cell voltage entries in MODULE_SENSORS: only register the cell
                # if it actually exists in the module data. Stack100 has 16 cells so
                # cell_17 through cell_30 will never be in mod and are skipped here.
                if data_key.startswith("cell_") and data_key[5:].isdigit():
                    if mod.get(data_key) is None:
                        continue
                new_entities.append(
                    DynessModuleSensor(
                        coordinator, entry, mid, data_key, trans_key,
                        unit, dev_cls, state_cls, icon, precision,
                    )
                )

            # Individual cell voltage sensors (disabled by default, user can enable in HA UI).
            # Only register cells that actually exist in the module data.
            # IMPORTANT: Stack100 sends 0.0 (not None) for unpopulated cell slots (cell_17-30).
            # We use the same v > 0 threshold as _parse_module_points to exclude those.
            for data_key, trans_key, unit, dev_cls, state_cls, icon, precision in _CELL_SENSORS:
                cell_val = mod.get(data_key)
                if cell_val is not None and float(cell_val) > 0:
                    new_entities.append(
                        DynessModuleSensor(
                            coordinator, entry, mid, data_key, trans_key,
                            unit, dev_cls, state_cls, icon, precision,
                            enabled_default=False,
                        )
                    )

        if new_entities:
            async_add_entities(new_entities)

    # Register modules already present on first coordinator refresh
    _add_new_modules()

    # Register any new modules that appear in subsequent updates
    entry.async_on_unload(coordinator.async_add_listener(_add_new_modules))


class DynessSensor(CoordinatorEntity, SensorEntity):

    def __init__(self, coordinator, entry, key, translation_key,
                 unit, device_class, state_class, icon, precision=None, entity_category=None):
        super().__init__(coordinator)
        self._key = key
        self._attr_translation_key            = translation_key
        self._attr_unique_id                  = f"{entry.entry_id}_{key}"
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class               = device_class
        self._attr_state_class                = state_class
        self._attr_has_entity_name            = True
        self._attr_icon                       = icon
        if precision is not None:
            self._attr_suggested_display_precision = precision
        if entity_category is not None:
            self._attr_entity_category = entity_category

    @property
    def device_info(self):
        di = self.coordinator.device_info
        return {
            "identifiers": {(DOMAIN, self.coordinator.device_sn)},
            "name": di.get("stationName", "Dyness Battery"),
            "manufacturer": "Dyness",
            "model": di.get("deviceModelName", "Dyness Battery"),
            "sw_version": di.get("firmwareVersion"),
        }

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get(self._key)

    @property
    def available(self):
        return self.coordinator.last_update_success and self.native_value is not None


# ── Modul-Sensoren ────────────────────────────────────────────────────────────

# Individual cell voltage sensors — disabled by default, user can enable in HA UI.
# All 30 are defined here; only those present in the module data dict are registered
# (see _add_new_modules above). Stack100: cells 01-16. Tower/TP7: cells 01-30.
_CELL_SENSORS = [
    (f"cell_{i:02d}", f"module_cell_{i:02d}",
     UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE,
     SensorStateClass.MEASUREMENT, "mdi:battery-outline", 3)
    for i in range(1, 31)
]

# MODULE_SENSORS intentionally does NOT include individual cell_XX entries —
# those go through _CELL_SENSORS with enabled_default=False.
# The cell_XX entries were previously appended here via a list comprehension,
# which caused all 30 cells to be registered (enabled=True) regardless of
# whether the module actually had that many cells. Removed in this fork.
MODULE_SENSORS = [
    ("soc",                   "module_soc",           PERCENTAGE,                   SensorDeviceClass.BATTERY,     SensorStateClass.MEASUREMENT,      "mdi:battery-high",           None),
    ("soh",                   "module_soh",           PERCENTAGE,                   SensorDeviceClass.BATTERY,     SensorStateClass.MEASUREMENT,      "mdi:battery-heart",          None),
    ("cycle_count",           "module_cycle_count",   None,                         None,                          SensorStateClass.TOTAL_INCREASING, "mdi:battery-sync",           None),
    ("cell_voltage_max",      "module_cell_v_max",    UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE,     SensorStateClass.MEASUREMENT,      "mdi:sine-wave",              3),
    ("cell_voltage_min",      "module_cell_v_min",    UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE,     SensorStateClass.MEASUREMENT,      "mdi:sine-wave",              3),
    ("cell_voltage_spread_mv","module_cell_spread",   "mV",                         None,                          SensorStateClass.MEASUREMENT,      "mdi:arrow-expand-horizontal", 1),
    ("bms_temp",              "module_temp_bms",      UnitOfTemperature.CELSIUS,    SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT,      "mdi:thermometer",            None),
    ("cell_temp_1",           "module_temp_1",        UnitOfTemperature.CELSIUS,    SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT,      "mdi:thermometer",            None),
    ("cell_temp_2",           "module_temp_2",        UnitOfTemperature.CELSIUS,    SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT,      "mdi:thermometer",            None),
    ("voltage",               "module_voltage",       UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE,     SensorStateClass.MEASUREMENT,      "mdi:sine-wave",              3),
    ("current",               "module_current",       UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT,     SensorStateClass.MEASUREMENT,      "mdi:current-dc",             None),
    ("has_alarm",             "module_alarm",         None,                         None,                          None,                              "mdi:alert-circle",           None),
]


class DynessModuleSensor(CoordinatorEntity, SensorEntity):
    """Sensor för ett enskilt sub-modul."""

    def __init__(self, coordinator, entry, module_id, data_key,
                 translation_key, unit, device_class, state_class, icon,
                 precision=None, enabled_default=True):
        super().__init__(coordinator)
        self._module_id   = module_id
        self._data_key    = data_key
        self._attr_translation_key                 = translation_key
        self._attr_unique_id                       = f"{entry.entry_id}_{module_id}_{data_key}"
        self._attr_native_unit_of_measurement      = unit
        self._attr_device_class                    = device_class
        self._attr_state_class                     = state_class
        self._attr_has_entity_name                 = True
        self._attr_icon                            = icon
        self._attr_entity_registry_enabled_default = enabled_default
        if precision is not None:
            self._attr_suggested_display_precision = precision

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, f"{self.coordinator.device_sn}_{self._module_id}")},
            "name": f"Dyness Module {self._module_id}",
            "manufacturer": "Dyness",
            "model": "Battery Module",
            "via_device": (DOMAIN, self.coordinator.device_sn),
        }

    @property
    def native_value(self):
        return (
            (self.coordinator.data or {})
            .get("module_data", {})
            .get(self._module_id, {})
            .get(self._data_key)
        )

    @property
    def available(self):
        return (
            self.coordinator.last_update_success
            and self._module_id in (self.coordinator.data or {}).get("module_data", {})
        )
