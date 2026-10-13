"""Disk usage of the NVR."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfInformation
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import NvrCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: NvrCoordinator = entry.runtime_data
    entities = []
    for disk in coordinator.data.info.disks:
        entities += [DiskSensor(coordinator, disk["id"], "free"), DiskSensor(coordinator, disk["id"], "used_percent")]
    async_add_entities(entities)


class DiskSensor(CoordinatorEntity[NvrCoordinator], SensorEntity):
    """Free space or usage of one NVR disk."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: NvrCoordinator, disk_id: str, kind: str) -> None:
        super().__init__(coordinator)
        info = coordinator.data.info
        self._disk_id = disk_id
        self._kind = kind
        self._attr_unique_id = f"{info.serial}_disk{disk_id}_{kind}"
        self._attr_translation_key = f"disk_{kind}"
        self._attr_translation_placeholders = {"disk": str(disk_id)}
        self._attr_state_class = SensorStateClass.MEASUREMENT
        if kind == "free":
            self._attr_device_class = SensorDeviceClass.DATA_SIZE
            self._attr_native_unit_of_measurement = UnitOfInformation.GIGABYTES
            self._attr_suggested_display_precision = 0
        else:
            self._attr_native_unit_of_measurement = PERCENTAGE
            self._attr_suggested_display_precision = 0
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, info.serial)},
            name=info.name if info.name != "Network Video Recorder" else info.model,
            manufacturer="EZVIZ",
            model=info.model,
            sw_version=info.firmware,
            serial_number=info.serial,
        )

    def _disk(self) -> dict | None:
        return next((d for d in self.coordinator.data.info.disks if d["id"] == self._disk_id), None)

    @property
    def native_value(self) -> float | None:
        disk = self._disk()
        if not disk or not disk["capacity_mb"]:
            return None
        if self._kind == "free":
            return round(disk["free_mb"] / 1024, 1)
        return round((1 - disk["free_mb"] / disk["capacity_mb"]) * 100, 1)

    @property
    def extra_state_attributes(self) -> dict:
        disk = self._disk() or {}
        return {"status": disk.get("status"), "capacity_gb": round((disk.get("capacity_mb") or 0) / 1024, 1)}
