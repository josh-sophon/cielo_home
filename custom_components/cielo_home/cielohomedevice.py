"""The Cielo Home integration."""

import asyncio
import contextlib
import logging
import sys
from threading import Lock, Timer
import time

from homeassistant.components.climate import HVACMode
from homeassistant.const import UnitOfTemperature

from .cielohome import CieloHome
from .const import (
    DEVICE_BREEZ_MAX,
    FAN_AUTO,
    FAN_AUTO_VALUE,
    FAN_FANSPEED_VALUE,
    FAN_HIGH,
    FAN_HIGH_VALUE,
    FAN_LOW,
    FAN_LOW_VALUE,
    FAN_MEDIUM,
    FAN_MEDIUM_VALUE,
    FOLLOW_ME_OFF,
    FOLLOW_ME_ON,
    PRESET_MODES,
    PRESET_NONE,
    PRESET_TURBO,
    SWING_ADJUST,
    SWING_ADJUST_VALUE,
    SWING_AUTO,
    SWING_AUTO_STOP,
    SWING_AUTO_STOP_VALUE,
    SWING_AUTO_VALUE,
    SWING_POSITION1,
    SWING_POSITION1_VALUE,
    SWING_POSITION2,
    SWING_POSITION2_VALUE,
    SWING_POSITION3,
    SWING_POSITION3_VALUE,
    SWING_POSITION4,
    SWING_POSITION4_VALUE,
    SWING_POSITION5,
    SWING_POSITION5_VALUE,
    SWING_POSITION6,
    SWING_POSITION6_VALUE,
)

_LOGGER = logging.getLogger(__name__)


class CieloHomeDevice:
    """Set up Cielo Home api."""

    def __init__(
        self,
        device,
        api: CieloHome,
        force_connection_source: bool,
        connection_source: bool,
        user_id,
    ) -> None:
        """Set up Cielo Home device."""
        self._api = api
        self._device = device
        self._timer_state_update: Timer = Timer(1, self.dispatch_state_updated)
        self.__event_listener: list[object] = []
        self._api.add_listener(self)
        self._timer_lock = Lock()
        self._force_connection_source = force_connection_source
        self._connection_source = 1 if connection_source else 0
        self._user_id = user_id
        self._old_power = self._device["latestAction"]["power"]
        # try:
        #    self._device["appliance"]["swing"] = ""
        #     self._device["appliance"]["fan"] = ""
        # except KeyError:
        #    pass

    def add_listener(self, listener: object) -> None:
        """None."""
        self.__event_listener.append(listener)

    def send_power_on(self) -> None:
        """None."""
        self._send_power("on")

    def send_power_off(self) -> None:
        """None."""
        self._send_power("off")

    def _send_power(self, value) -> None:
        """None."""
        if self._device["latestAction"]["power"] == value:
            return

        if self._is_ct01():
            self._device["latestAction"]["power"] = value
            if value == "off":
                self._device["latestAction"]["mode"] = self._CT01_NUM_TO_MODE.get(
                    self._get_ct01_prefs().get("previousMode", 3), "auto"
                )
                action_str = self._build_ct01_action_string(mode=0)
            else:
                # Turn on to previous mode
                prev = self._get_ct01_prefs().get("previousMode", 3)
                mode_num = prev if prev != 0 else 3
                self._device["latestAction"]["mode"] = self._CT01_NUM_TO_MODE.get(mode_num, "auto")
                action_str = self._build_ct01_action_string(mode=mode_num)
            self._send_ct01_command(action_str)
            return

        action = self._get_action()
        action["power"] = value
        self._device["latestAction"]["power"] = value
        self._send_msg(action, "power", action["power"])

    #    def sync_ac_state_test(
    #        self, power: bool, temp: int, mode: str, fan_speed: str, swing: str, preset: str
    #    ) -> None:
    #        """None."""
    #        action = self._get_action()
    #        action["power"] = "on" if power else "off"
    #
    #        if power:
    #            if temp > 0:
    #                action["temp"] = temp
    #
    #            if mode != "":
    #                action["mode"] = mode
    #
    #            if fan_speed != "":
    #                action["fanspeed"] = fan_speed
    #
    #            if swing != "":
    #                action["swing"] = swing
    #
    #            if preset != "":
    #                action["preset"] = preset
    #
    #        self._send_msg(action, "", "", default_action="actionControl")

    def sync_ac_state(
        self, power: bool, temp: int, mode: str, fan_speed: str, swing: str, preset: str
    ) -> None:
        """None."""
        action = self._get_action()
        action = {
            "power": "on" if power else "off",
            "temp": temp
            if power and temp > 0
            else self._device["latestAction"]["temp"],
            "mode": mode
            if power and mode != ""
            else self._device["latestAction"]["mode"],
            "fanspeed": fan_speed
            if power and fan_speed != ""
            else self._device["latestAction"]["fanspeed"],
            "swing": swing
            if power and swing != ""
            else self._device["latestAction"]["swing"],
            "preset": preset
            if power and preset != ""
            else self._device["latestAction"]["turbo"],
        }
        self._send_msg(action, "", "", default_action="syncState")

    def send_screenbacklight_off(self) -> None:
        self._device["deviceSettings"]["screenDisplayValue"] = "0"
        self._send_msg_setting("H0")

    def send_screenbacklight_on(self) -> None:
        self._device["deviceSettings"]["screenDisplayValue"] = "1"
        self._send_msg_setting("H1")

    def send_screenbacklightBrightness(self, value: int) -> None:
        self._device["deviceSettings"]["brightnessValue"] = str(value)
        self._send_msg_setting("H2" + str(value))

    def send_light_on(self) -> None:
        """None."""
        self._send_light("on")

    def send_light_off(self) -> None:
        """None."""
        self._send_light("off")

    def _send_light(self, value) -> None:
        """None."""
        # if self._device["latestAction"]["light"] == value:
        #     return

        action = self._get_action()
        action["light"] = value
        self._device["latestAction"]["light"] = value
        self._send_msg(action, "light", "on/off")

    def send_turbo_on(self) -> None:
        """None."""
        self._send_turbo("on")

    def send_turbo_off(self) -> None:
        """None."""
        self._send_turbo("off")

    def _send_turbo(self, value) -> None:
        """None."""
        if self._device["latestAction"]["turbo"] == value:
            return

        action = self._get_action()
        action["turbo"] = value
        self._device["latestAction"]["turbo"] = value

        # Keep original logic but fix the OR condition
        if (
            self.get_device_type_version() != "BI03"
            and self.get_device_type_version() != "BI04"
        ):
            action_value = "on/off"
        else:
            action_value = value

        _LOGGER.debug(
            f"Sending turbo command: actionValue='{action_value}' for device {self.get_name()}"
        )
        self._send_msg(action, "turbo", action_value)

    def _send_preset_mode(self, value: int) -> None:
        """None."""
        if (
            self._device["latestAction"]["mode"] == "auto"
            and self._device["latestAction"]["preset"] == value
        ):
            return

        action = self._get_action()
        action["mode"] = "auto"
        self._device["latestAction"]["mode"] = "auto"
        self._device["latestAction"]["preset"] = value

        self._send_msg(action, "mode", "auto", overrides={"preset": value})

    def _get_base_msg(
        self,
        action,
        actionSource="WEB",
    ) -> dict:
        return {
            "action": action,
            "actionSource": actionSource,
            "macAddress": self.get_mac_address(),
            "user_id": self._user_id,
            "fw_version": self.get_fw_version(),
            "deviceTypeVersion": self.get_device_type_version(),
            "mid": "",
            "connection_source": self._connection_source
            if self._force_connection_source
            else self.get_connection_source(),
            "application_version": "1.4.4",
            "ts": 0,
        }

    def _send_msg_setting(self, action_string) -> None:
        msg = self._get_base_msg("deviceSettings")
        msg["mid"] = "WEB"
        msg["actionString"] = action_string
        self._api.send_action(msg)

    def _send_msg(
        self,
        action,
        action_type,
        action_value,
        default_action="actionControl",
        overrides=None,
    ) -> None:
        msg = self._get_base_msg(default_action)

        msg["mid"] = "WEB"
        msg["fwVersion"] = self.get_fw_version()
        msg["applianceType"] = self.get_appliance_type()
        msg["applianceId"] = self.get_appliance_id()
        msg["myRuleConfiguration"] = self.get_my_rule_configuration()
        msg["user_id"] = self._user_id
        msg["preset"] = 0
        msg["actions"] = action
        msg["oldPower"] = self._old_power

        if default_action == "actionControl":
            msg["actionType"] = action_type
            msg["actionValue"] = action_value

        if overrides:
            msg = {**msg, **overrides}

        self._api.send_action(msg)
        # self._api.send_action(msg)

    def send_mode_heat(self) -> None:
        """None."""
        self._send_mode("heat")

    def send_mode_cool(self) -> None:
        """None."""
        value = "cool"
        if self.get_available_modes() == "mode":
            value = "mode"

        self._send_mode(value)

    def send_mode_dry(self) -> None:
        """None."""
        self._send_mode("dry")

    def send_mode_auto(self) -> None:
        """None."""
        self._send_mode("auto")

    def send_mode_fan(self) -> None:
        """None."""
        self._send_mode("fan")

    def send_mode_freezepoint(self) -> None:
        """None."""
        if self.get_hvac_mode() != HVACMode.HEAT:
            self.send_mode_heat()
            time.sleep(2)

        self._send_mode("freezepoint")

    def _send_mode(self, value) -> None:
        """None."""
        if self._is_ct01():
            mode_num = self._CT01_MODE_TO_NUM.get(value, 3)
            self._device["latestAction"]["power"] = "off" if mode_num == 0 else "on"
            self._device["latestAction"]["mode"] = value
            action_str = self._build_ct01_action_string(mode=mode_num)
            self._send_ct01_command(action_str)
            return

        if self.get_power() == "off":
            self.send_power_on()
            time.sleep(2)

        if self._device["latestAction"]["mode"] == value and value not in (
            "freezepoint",
            "mode",
        ):
            return

        action = self._get_action()

        action["mode"] = value if value not in ("freezepoint") else "heat"
        # action["mode"] = value
        self._device["latestAction"]["mode"] = value
        self._send_msg(action, "mode", value)

    def send_fan_speed_medium(self) -> None:
        """None."""
        self._send_fan_speed(FAN_MEDIUM_VALUE)

    def send_fan_speed_high(self) -> None:
        """None."""
        self._send_fan_speed(FAN_HIGH_VALUE)

    def send_fan_speed_low(self) -> None:
        """None."""
        self._send_fan_speed(FAN_LOW_VALUE)

    def send_fan_speed_auto(self) -> None:
        """None."""
        self._send_fan_speed(FAN_AUTO_VALUE)

    def send_fan_speed_rotate(self) -> None:
        """None."""
        self._send_fan_speed(FAN_FANSPEED_VALUE)

    def _send_fan_speed(self, value) -> None:
        """None."""
        if self._device["latestAction"]["fanspeed"] == value:
            return

        action = self._get_action()
        action["fanspeed"] = value
        self._device["latestAction"]["fanspeed"] = value
        self._send_msg(action, "fanspeed", action["fanspeed"])

    def send_follow_me_on(self) -> None:
        """None."""
        self.send_follow_me(FOLLOW_ME_ON)

    def send_follow_me_off(self) -> None:
        """None."""
        self.send_follow_me(FOLLOW_ME_OFF)

    def send_follow_me(self, value) -> None:
        """None."""
        if self._device["latestAction"]["followme"] == value:
            return

        action = self._get_action()
        action["followme"] = value
        self._device["latestAction"]["followme"] = value
        self._send_msg(action, "followme", action["followme"])

    def send_swing_adjust(self) -> None:
        """None."""
        self._send_swing(SWING_ADJUST_VALUE)

    def send_swing_auto(self) -> None:
        """None."""
        self._send_swing(SWING_AUTO_VALUE)

    def send_swing_auto_stop(self) -> None:
        """None."""
        self._send_swing(SWING_AUTO_STOP_VALUE)

    def send_swing_pos1(self) -> None:
        """None."""
        self._send_swing(SWING_POSITION1_VALUE)

    def send_swing_pos2(self) -> None:
        """None."""
        self._send_swing(SWING_POSITION2_VALUE)

    def send_swing_pos3(self) -> None:
        """None."""
        self._send_swing(SWING_POSITION3_VALUE)

    def send_swing_pos4(self) -> None:
        """None."""
        self._send_swing(SWING_POSITION4_VALUE)

    def send_swing_pos5(self) -> None:
        """None."""
        self._send_swing(SWING_POSITION5_VALUE)

    def send_swing_pos6(self) -> None:
        """None."""
        self._send_swing(SWING_POSITION6_VALUE)

    def _send_swing(self, value) -> None:
        """None."""
        if self._device["latestAction"]["swing"] == value:
            return

        action = self._get_action()
        action["swing"] = value
        self._device["latestAction"]["swing"] = value
        self._send_msg(action, "swing", action["swing"])

    def send_temperature(self, value) -> None:
        """None."""
        if self._is_ct01():
            temp_str = str(int(value))
            self._device["latestAction"]["temp"] = temp_str
            mode = self.get_mode()
            if mode == "cool":
                action_str = self._build_ct01_action_string(
                    cool_sp=temp_str, auto_cool_sp=temp_str
                )
            elif mode in ("heat", "freezepoint"):
                action_str = self._build_ct01_action_string(
                    heat_sp=temp_str, auto_heat_sp=temp_str
                )
            else:
                # auto mode — set the cool setpoint (more common use case in PR)
                action_str = self._build_ct01_action_string(
                    auto_cool_sp=temp_str
                )
            self._send_ct01_command(action_str)
            return

        actionValue = value
        temp = int(self._device["latestAction"]["temp"])
        if temp == int(value) and self.get_supportTargetTemp():
            return

        if not self.get_supportTargetTemp():
            if temp < int(value):
                actionValue = "inc"
                value = int(value) - 1
            else:
                actionValue = "dec"
                value = int(value) + 1

        action = self._get_action()
        action["temp"] = str(value)
        self._device["latestAction"]["temp"] = action["temp"]
        self._send_msg(action, "temp", actionValue)

    def send_temperatureUp(self) -> None:
        """None."""
        self.send_temperature(int(self._device["latestAction"]["temp"]) + 1)

    def send_temperatureDown(self) -> None:
        """None."""
        self.send_temperature(int(self._device["latestAction"]["temp"]) - 1)

    def get_current_temperature(self) -> float:
        """None."""
        try:
            temp_value = self._device.get("latEnv", {}).get("temp", 0)
            if temp_value is None:
                return 0.0
            return float(temp_value)
        except (ValueError, TypeError) as e:
            _LOGGER.error(
                "temp value '%s' not supported: %s",
                self._device.get("latEnv", {}).get("temp", "None"),
                e,
            )
            return 0.0

    def get_humidity(self) -> float:
        """None."""
        try:
            humidity_value = self._device.get("latEnv", {}).get("humidity", 0)
            if humidity_value is None:
                return 0.0
            return float(humidity_value)
        except (ValueError, TypeError) as e:
            _LOGGER.error(
                "humidity value '%s' not supported: %s",
                self._device.get("latEnv", {}).get("humidity", "None"),
                e,
            )
            return 0.0

    def get_is_device_fahrenheit(self) -> bool:
        """None."""
        return self._device["isFaren"] == 1

    def get_is_appliance_fahrenheit(self) -> bool:
        """None."""
        return self._device["appliance"]["isFaren"] == 1

    def get_temp_increment(self) -> float:
        """None."""
        return self._device["appliance"]["tempIncrement"]

    def get_available_modes(self) -> str:
        """None."""
        return self._device["appliance"]["mode"]

    def get_available_fan_modes(self) -> str:
        """None."""
        return self._device["appliance"]["fan"]

    def get_is_fan_mode_cycle(self) -> bool:
        """None."""
        return self._device["appliance"]["fan"] == "fanspeed"

    def get_available_swing_modes(self) -> str:
        """None."""
        with contextlib.suppress(KeyError):
            return self._device["appliance"]["swing"]

    def get_is_available_swing_modes(self) -> bool:
        """None."""
        with contextlib.suppress(KeyError):
            return self.get_available_swing_modes().strip() != ""

    def get_is_appliance_is_freezepoin_display(self) -> bool:
        """None."""
        with contextlib.suppress(KeyError):
            return self._device["appliance"]["isFreezepointDisplay"] == 1

    def get_is_light_mode(self) -> bool:
        """None."""
        try:
            return self._device["appliance"]["isDisplayLight"] == 1
        except KeyError:
            pass

    def get_is_turbo_mode(self) -> bool:
        """None."""
        with contextlib.suppress(KeyError):
            return self._device["appliance"]["turbo"] != ""

        return False

    def get_is_followme_mode(self) -> bool:
        """None."""
        with contextlib.suppress(KeyError):
            return self._device["appliance"]["followme"] != ""

        return False

    def get_supportTargetTemp(self) -> bool:
        """None."""
        if self._device["appliance"]["temp"] == "inc:dec":
            return False
        else:
            return True

    def get_range_temp(self) -> str:
        """None."""
        if self.get_is_multi_mode_Temp_Range():
            modes = self.get_modes_temp()
            if modes is not None:
                for mode in modes:
                    if mode["mode"] == self.get_mode():
                        return mode["temp"]

        return self._device["appliance"]["temp"]

    def get_is_multi_mode_Temp_Range(self) -> bool:
        """None."""
        with contextlib.suppress(KeyError):
            return self._device["appliance"]["isMultiModeTempRange"] == 1

        return False

    def get_modes_temp(self) -> any:
        """None."""
        with contextlib.suppress(KeyError):
            return self._device["appliance"]["modesTemp"]
        return None

    def get_uniqueid(self):
        """None."""
        return self.get_mac_address()

    def get_mac_address(self) -> str:
        """None."""
        return self._device["macAddress"]

    def get_name(self) -> str:
        """None."""
        return self._device["deviceName"]

    def get_version(self) -> str:
        """None."""
        return self._device["fwVersion"]

    def get_device_type_version(self) -> str:
        """None."""
        return self._device["deviceTypeVersion"]

    def get_device_type(self) -> str:
        """None."""
        return self._device["deviceType"]

    def get_fw_version(self) -> str:
        """None."""
        return self._device["fwVersion"]

    def get_appliance_id(self):
        """None."""
        return self._device.get("applianceId", 0)

    def get_my_rule_configuration(self) -> any:
        """None."""
        with contextlib.suppress(KeyError):
            return self._device["myRuleConfiguration"]
        return {}

    def get_connection_source(self) -> int:
        """None."""
        return self._device["connectionSource"]

    def get_screenDisplayIsOn(self) -> bool:
        """None."""
        return self._device["deviceSettings"]["screenDisplayValue"] == "1"

    def get_screenDisplay_available(self) -> bool:
        """None."""
        with contextlib.suppress(KeyError):
            return self._device["deviceSettings"]["screenDisplayValue"] != ""

        return False

    def get_screenIdleScreenTimeout_value(self) -> str:
        """None."""
        with contextlib.suppress(KeyError):
            return self._device["deviceSettings"]["idleScreenTimeout"]

        return ""

    def get_screenIdleScreenTimeout_available(self) -> bool:
        """None."""
        with contextlib.suppress(KeyError):
            return self._device["deviceSettings"]["idleScreenTimeout"] != ""

        return False

    def get_screenbrightness_value(self) -> str:
        """None."""
        with contextlib.suppress(KeyError):
            return self._device["deviceSettings"]["brightnessValue"]

        return ""

    def get_screenbrightness_available(self) -> bool:
        """None."""
        with contextlib.suppress(KeyError):
            return self._device["deviceSettings"]["brightnessValue"] != ""

        return False

    def get_screenidlebrightness_available(self) -> bool:
        """None."""
        with contextlib.suppress(KeyError):
            return self._device["deviceSettings"]["idleBrightnessValue"] != ""

        return False

    def get_screenidlebrightness_value(self) -> str:
        """None."""
        with contextlib.suppress(KeyError):
            return self._device["deviceSettings"]["idleBrightnessValue"]

        return False

    def get_appliance_type(self) -> str:
        """None."""
        return self._device.get("applianceType", "")

    def get_device(self):
        """None."""
        return self._device

    def get_mode(self) -> str:
        """None."""
        return self._device["latestAction"]["mode"]

    def get_power(self) -> str:
        """None."""
        return self._device["latestAction"]["power"]

    def get_follow_me(self) -> str:
        """None."""
        return self._device["latestAction"]["followme"]

    def get_light(self) -> str:
        """None."""
        with contextlib.suppress(KeyError):
            return (
                "off"
                if self._device["latestAction"]["light"] == "on/off"
                else self._device["latestAction"]["light"]
            )

        return ""

    def get_target_temperature(self) -> float:
        """None."""
        return float(self._device["latestAction"]["temp"])

    def get_turbo(self) -> str:
        """None."""
        with contextlib.suppress(KeyError):
            return self._device["latestAction"]["turbo"]

        return "off"

    def get_fanspeed(self) -> str:
        """None."""
        return self._device["latestAction"]["fanspeed"]

    def get_swing(self) -> str:
        """None."""
        with contextlib.suppress(KeyError):
            return self._device["latestAction"]["swing"]
        return ""

    def get_status(self) -> bool:
        """None."""
        return (
            self._device["deviceStatus"] == 1
            or str(self._device["deviceStatus"]) == "on"
        )

    def get_status_str(self) -> str:
        """None."""
        return "on" if self.get_status() else "off"

    def _get_action(self) -> object:
        """None."""
        action = {
            "power": self._device["latestAction"]["power"],
            "mode": self._device["latestAction"]["mode"],
            "fanspeed": self._device["latestAction"]["fanspeed"],
            "temp": self._device["latestAction"]["temp"],
            "swing": self._device["latestAction"]["swing"],
            "swinginternal": "",
        }

        with contextlib.suppress(KeyError):
            action["turbo"] = self._device["latestAction"]["turbo"]

        try:
            action["light"] = (
                "off"
                if self._device["latestAction"]["light"] == "on/off"
                else self._device["latestAction"]["light"]
            )
        except KeyError:
            if self.get_available_modes() != "mode":
                action["light"] = "off"

        with contextlib.suppress(KeyError):
            action["followme"] = self._device["latestAction"]["followme"]

        return action

    def get_fan_modes(self) -> list[str]:
        """None."""
        modes = self.get_available_fan_modes()
        modes_list = modes.split(":")
        fan_modes: list = []
        for mode in modes_list:
            if mode == "auto":
                fan_modes.append(FAN_AUTO)
            elif mode == "low":
                fan_modes.append(FAN_LOW)
            elif mode == "medium":
                fan_modes.append(FAN_MEDIUM)
            elif mode == "high":
                fan_modes.append(FAN_HIGH)

        if len(fan_modes) > 0:
            return fan_modes

        return None

    def get_max_temp(self) -> float:
        """None."""
        with contextlib.suppress(Exception):
            range_temp: str = self.get_range_temp()
            device_unit: str = self.get_unit_of_temperature_appliance()
            range_temps: list = range_temp.split(":")

            return self.get_adjust_temp(
                self.get_unit_of_temperature(), device_unit, int(range_temps[1])
            )

        return -1

    def get_min_temp(self) -> float:
        """None."""
        with contextlib.suppress(Exception):
            range_temp: str = self.get_range_temp()
            device_unit: str = self.get_unit_of_temperature_appliance()

            range_temps: list = range_temp.split(":")

            return self.get_adjust_temp(
                self.get_unit_of_temperature(), device_unit, int(range_temps[0])
            )

        return -1

    def get_adjust_temp(
        self, target_unit_temp: str, current_unit_temp: str, temp: int
    ) -> float:
        """Set the system mode."""
        if (
            current_unit_temp == UnitOfTemperature.CELSIUS
            and target_unit_temp == UnitOfTemperature.FAHRENHEIT
        ):
            return int((temp * 18) + 32)
        elif (
            current_unit_temp == UnitOfTemperature.FAHRENHEIT
            and target_unit_temp == UnitOfTemperature.CELSIUS
        ):
            return int((temp - 32) / 1.8)
        else:
            return temp

    def get_fan_mode(self) -> str:
        """None."""
        if self.get_fanspeed() == "auto":
            return FAN_AUTO
        elif self.get_fanspeed() == "low":
            return FAN_LOW
        elif self.get_fanspeed() == "medium":
            return FAN_MEDIUM
        elif self.get_fanspeed() == "high":
            return FAN_HIGH
        else:
            return FAN_AUTO

    def get_hvac_mode(self) -> str:
        """None."""
        if self.get_power() == "off":
            return HVACMode.OFF
        elif self.get_mode() == "auto":
            return HVACMode.AUTO
        elif self.get_mode() == "heat" or self.get_mode() == "freezepoint":
            return HVACMode.HEAT
        elif self.get_mode() == "cool" or self.get_mode() == "mode":
            return HVACMode.COOL
        elif self.get_mode() == "dry":
            return HVACMode.DRY
        elif self.get_mode() == "fan":
            return HVACMode.FAN_ONLY
        else:
            return HVACMode.OFF

    def get_hvac_modes(self) -> list[str]:
        """None."""
        modes: str = self.get_available_modes()
        if modes == "mode":
            modes = "cool"

        modes_list: list = modes.split(":")
        hvac_modes: list = [HVACMode.OFF]
        for mode in modes_list:
            if mode == "auto":
                hvac_modes.append(HVACMode.AUTO)
            elif mode == "cool":
                hvac_modes.append(HVACMode.COOL)
            elif mode == "dry":
                hvac_modes.append(HVACMode.DRY)
            elif mode == "fan":
                hvac_modes.append(HVACMode.FAN_ONLY)
            elif mode == "heat":
                hvac_modes.append(HVACMode.HEAT)
            else:
                pass

        if len(hvac_modes) > 0:
            return hvac_modes

        return None

    def get_swing_mode(self) -> str:
        """None."""
        if self.get_swing() == "auto":
            return SWING_AUTO
        elif self.get_swing() == "adjust":
            return SWING_ADJUST
        elif self.get_swing() == "auto/stop":
            return SWING_AUTO_STOP
        elif self.get_swing() == "pos1":
            return SWING_POSITION1
        elif self.get_swing() == "pos2":
            return SWING_POSITION2
        elif self.get_swing() == "pos3":
            return SWING_POSITION3
        elif self.get_swing() == "pos4":
            return SWING_POSITION4
        elif self.get_swing() == "pos5":
            return SWING_POSITION5
        elif self.get_swing() == "pos6":
            return SWING_POSITION6
        else:
            pass

    def get_swing_modes(self) -> list[str]:
        """None."""
        modes = self.get_available_swing_modes()
        if modes is not None:
            modes_list = modes.split(":")
            swing_modes: list = []
            for mode in modes_list:
                if mode == "auto/stop":
                    swing_modes.append(SWING_AUTO_STOP)
                elif mode == "auto":
                    swing_modes.append(SWING_AUTO)
                elif mode == "adjust":
                    swing_modes.append(SWING_ADJUST)
                elif mode == "pos1":
                    swing_modes.append(SWING_POSITION1)
                elif mode == "pos2":
                    swing_modes.append(SWING_POSITION2)
                elif mode == "pos3":
                    swing_modes.append(SWING_POSITION3)
                elif mode == "pos4":
                    swing_modes.append(SWING_POSITION4)
                elif mode == "pos5":
                    swing_modes.append(SWING_POSITION5)
                elif mode == "pos6":
                    swing_modes.append(SWING_POSITION6)
                else:
                    pass

            if len(swing_modes) > 0:
                return swing_modes

        return None

    def get_preset_mode(self) -> str:
        """None."""
        if self.get_device_type() == DEVICE_BREEZ_MAX:
            preset_modes = self.get_breez_preset_modes()
            for key in preset_modes.keys():
                if preset_modes[key] == self._device["latestAction"]["preset"]:
                    return key

        if self.get_turbo() == "on":
            return PRESET_TURBO
        else:
            return PRESET_NONE

    def get_breez_preset_modes(self) -> dict[str:int]:
        """None."""
        with contextlib.suppress(KeyError):
            presets = self._device["breezPresets"]
            if presets:
                result = {preset["title"]: preset["presetId"] for preset in presets}
                return {**{"": 0}, **result}
        return []

    def get_preset_modes(self) -> list[str]:
        """None."""
        if self.get_device_type() == DEVICE_BREEZ_MAX:
            return list(self.get_breez_preset_modes().keys())

        if self.get_is_turbo_mode():
            return PRESET_MODES
        else:
            return None

    def get_unit_of_temperature(self) -> str:
        """None."""
        return (
            UnitOfTemperature.FAHRENHEIT
            if self.get_is_device_fahrenheit()
            else UnitOfTemperature.CELSIUS
        )

    def get_unit_of_temperature_appliance(self) -> str:
        """None."""
        return (
            UnitOfTemperature.FAHRENHEIT
            if self.get_is_appliance_fahrenheit()
            else UnitOfTemperature.CELSIUS
        )

    def send_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """None."""
        if hvac_mode == HVACMode.OFF:
            self.send_power_off()
        elif hvac_mode == HVACMode.AUTO:
            self.send_mode_auto()
        elif hvac_mode == HVACMode.HEAT:
            self.send_mode_heat()
        elif hvac_mode == HVACMode.DRY:
            self.send_mode_dry()
        elif hvac_mode == HVACMode.COOL:
            self.send_mode_cool()
        elif hvac_mode == HVACMode.FAN_ONLY:
            self.send_mode_fan()
        else:
            pass

    def send_preset_mode(self, preset_mode: str) -> None:
        """None."""
        if self.get_device_type() == DEVICE_BREEZ_MAX:
            preset_id = self.get_breez_preset_modes().get(preset_mode)
            self._send_preset_mode(preset_id)
        else:
            if preset_mode == PRESET_TURBO:
                self.send_turbo_on()
            else:
                self.send_turbo_off()

    def send_swing_mode(self, swing_mode: str) -> None:
        """None."""
        if swing_mode == SWING_AUTO:
            self.send_swing_auto()
        elif swing_mode == SWING_AUTO_STOP:
            self.send_swing_auto_stop()
        elif swing_mode == SWING_ADJUST:
            self.send_swing_adjust()
        elif swing_mode == SWING_POSITION1:
            self.send_swing_pos1()
        elif swing_mode == SWING_POSITION2:
            self.send_swing_pos2()
        elif swing_mode == SWING_POSITION3:
            self.send_swing_pos3()
        elif swing_mode == SWING_POSITION4:
            self.send_swing_pos4()
        elif swing_mode == SWING_POSITION5:
            self.send_swing_pos5()
        elif swing_mode == SWING_POSITION6:
            self.send_swing_pos6()
        else:
            pass

    def send_fan_mode(self, fan_mode: str) -> None:
        """None."""
        if fan_mode == FAN_AUTO:
            self.send_fan_speed_auto()
        elif fan_mode == FAN_HIGH:
            self.send_fan_speed_high()
        elif fan_mode == FAN_MEDIUM:
            self.send_fan_speed_medium()
        elif fan_mode == FAN_LOW:
            self.send_fan_speed_low()
        else:
            pass

    def _is_ct01(self) -> bool:
        """Check if this device is a CT01 wired thermostat."""
        return (
            self._device.get("deviceType") == "THERMOSTAT"
            or self.get_device_type_version().startswith("CT")
        )

    # --- CT01 thermostat command support ---

    _CT01_MODE_TO_NUM = {"off": 0, "heat": 1, "cool": 2, "auto": 3}
    _CT01_NUM_TO_MODE = {0: "off", 1: "heat", 2: "cool", 3: "auto", 4: "heat"}

    def _get_ct01_prefs(self):
        """Get CT01 preferences, falling back to synthesised latestAction."""
        return self._device.get("preferences", {})

    def _build_ct01_action_string(
        self,
        mode: int | None = None,
        heat_sp: str | None = None,
        cool_sp: str | None = None,
        auto_heat_sp: str | None = None,
        auto_cool_sp: str | None = None,
    ) -> str:
        """Build a CT01 action_string from current state + overrides.

        Format: S,{preset},{mode},{heatSP},{coolSP},{autoHeatSP},{autoCoolSP},
                  {fanHeat},{auxStage},{fanTimer},{holdActive},{fanCool},
                  {f12},{f13},{f14},{f15},{f16},
        """
        prefs = self._get_ct01_prefs()
        manual = prefs.get("manualSettings", {})

        p_mode = mode if mode is not None else prefs.get("mode", 0)
        p_heat = heat_sp or str(manual.get("heatSetPoint", "65.0"))
        p_cool = cool_sp or str(manual.get("coolSetPoint", "73.0"))
        p_aheat = auto_heat_sp or str(manual.get("autoHeatSetPoint", "65.0"))
        p_acool = auto_cool_sp or str(manual.get("autoCoolSetPoint", "73.0"))
        p_fan_heat = manual.get("fanHeat", 2)
        p_fan_cool = manual.get("fanCool", 2)
        p_aux = prefs.get("auxStage", 0)
        p_fan_timer = prefs.get("fanTimerDuration", 15)
        p_hold = prefs.get("holdSettingsActive", 0)

        fields = [
            "S",
            str(prefs.get("activePresetId", 0)),
            str(p_mode),
            p_heat,
            p_cool,
            p_aheat,
            p_acool,
            str(p_fan_heat),
            str(p_aux),
            str(p_fan_timer),
            str(p_hold),
            str(p_fan_cool),
            str(prefs.get("coolingSmartRecovery", 0)),
            str(prefs.get("heatingSmartRecovery", 0)),
            "0",
            "0",
            str(prefs.get("previousMode", 0)),
            "",  # trailing comma
        ]
        return ",".join(fields)

    def _send_ct01_command(self, action_string: str) -> None:
        """Send a thermostatActions command for CT01 devices."""
        msg = {
            "action": "thermostatActions",
            "deviceTypeVersion": self.get_device_type_version(),
            "user_id": self._user_id,
            "mac_address": self.get_mac_address(),
            "connection_source": self._connection_source
            if self._force_connection_source
            else self.get_connection_source(),
            "application_version": "1.4.7",
            "action_string": action_string,
            "mid": "WEB",
            "ts": 0,
        }
        _LOGGER.debug("CT01 command: %s", action_string)
        self._api.send_action(msg)

    def _data_receive_ct01(self, data) -> None:
        """Handle CT01 thermostat state updates.

        CT01 StateUpdate uses action.equipment_power, action.mode (numeric),
        and action.manual_settings — completely different from Breez format.
        """
        with contextlib.suppress(KeyError):
            self._device["latEnv"]["temp"] = data["lat_env_var"]["temperature"]
            self._device["latEnv"]["humidity"] = data["lat_env_var"]["humidity"]

        self._device["deviceStatus"] = data.get("device_status", self._device.get("deviceStatus", 1))

        action = data.get("action", {})

        # CT01 StateUpdate has equipment_power, mode (numeric), manual_settings
        if "equipment_power" in action:
            power = action["equipment_power"]
            self._device["latestAction"]["power"] = power
            self._old_power = power

            mode_num = action.get("mode", 0)
            if isinstance(mode_num, (int, str)):
                mode_num = int(mode_num)
                mode_str = self._CT01_NUM_TO_MODE.get(mode_num, "auto")
                self._device["latestAction"]["mode"] = mode_str

            # Update target temp from manual_settings
            manual = action.get("manual_settings", {})
            if manual:
                if mode_num == 2:  # cool
                    sp = manual.get("cool_set_point", manual.get("auto_cool_set_point"))
                elif mode_num in (1, 4):  # heat
                    sp = manual.get("heat_set_point", manual.get("auto_heat_set_point"))
                elif mode_num == 3:  # auto
                    sp = manual.get("auto_cool_set_point")
                else:
                    sp = manual.get("cool_set_point")

                if sp is not None:
                    self._device["latestAction"]["temp"] = str(sp).replace(".0", "")

            # Update fan
            fan_num = action.get("fan", 0)
            self._device["latestAction"]["fanspeed"] = "low" if fan_num == 1 else "auto"

            # Persist preferences so _build_ct01_action_string stays current
            prefs = self._device.get("preferences", {})
            prefs["equipmentPower"] = power
            prefs["mode"] = mode_num
            prefs.update({
                k: action[k] for k in (
                    "active_preset_id", "hold_settings_active", "fan",
                    "previous_mode", "aux_stage", "fan_timer_duration",
                    "cooling_smart_recovery", "heating_smart_recovery",
                ) if k in action
            })
            # Map snake_case manual_settings back to camelCase preferences
            if manual:
                pm = prefs.setdefault("manualSettings", {})
                _SNAKE_TO_CAMEL = {
                    "heat_set_point": "heatSetPoint",
                    "cool_set_point": "coolSetPoint",
                    "auto_heat_set_point": "autoHeatSetPoint",
                    "auto_cool_set_point": "autoCoolSetPoint",
                    "fan_heat": "fanHeat",
                    "fan_cool": "fanCool",
                }
                for sk, ck in _SNAKE_TO_CAMEL.items():
                    if sk in manual:
                        pm[ck] = manual[sk]
            # Also sync snake_case prefs keys to camelCase
            _PREF_MAP = {
                "active_preset_id": "activePresetId",
                "hold_settings_active": "holdSettingsActive",
                "previous_mode": "previousMode",
                "aux_stage": "auxStage",
                "fan_timer_duration": "fanTimerDuration",
                "cooling_smart_recovery": "coolingSmartRecovery",
                "heating_smart_recovery": "heatingSmartRecovery",
            }
            for sk, ck in _PREF_MAP.items():
                if sk in action:
                    prefs[ck] = action[sk]

    def data_receive(self, data) -> None:
        """None."""
        if data["mac_address"] == self.get_mac_address():
            if self._is_ct01():
                with contextlib.suppress(Exception):
                    if data.get("message_type") in ("StateUpdate", "DeviceSettingsAck"):
                        self._data_receive_ct01(data)
                self.dispatch_state_timer()
                return

            with contextlib.suppress(KeyError):
                if data["message_type"] == "StateUpdate":
                    self._device["latEnv"]["temp"] = data["lat_env_var"]["temperature"]
                    self._device["latEnv"]["humidity"] = data["lat_env_var"]["humidity"]
                    if (
                        data["device_status"] == 0
                        and data["action"]["device_status"] == "on"
                    ):
                        self._device["deviceStatus"] = 1
                    else:
                        self._device["deviceStatus"] = data["device_status"]
                    self._device["latestAction"]["temp"] = data["action"]["temp"]
                    self._device["latestAction"]["fanspeed"] = data["action"][
                        "fanspeed"
                    ]
                    self._device["latestAction"]["mode"] = data["action"]["mode"]
                    self._device["latestAction"]["power"] = data["action"]["power"]
                    self._old_power = self._device["latestAction"]["power"]

                    with contextlib.suppress(KeyError):
                        self._device["latestAction"]["swing"] = data["action"]["swing"]

                    with contextlib.suppress(KeyError):
                        self._device["latestAction"]["turbo"] = data["action"]["turbo"]

                    with contextlib.suppress(KeyError):
                        self._device["latestAction"]["light"] = data["action"]["light"]

                    with contextlib.suppress(KeyError):
                        self._device["latestAction"]["followme"] = data["action"][
                            "followme"
                        ]

                    with contextlib.suppress(KeyError):
                        self._device["latestAction"]["preset"] = data["action"][
                            "preset"
                        ]

            with contextlib.suppress(KeyError):
                if data["message_type"] == "DeviceSettingsAck":
                    if data["H"] == "1":
                        self._device["deviceSettings"]["screenDisplayValue"] = "1"
                    else:
                        self._device["deviceSettings"]["screenDisplayValue"] = "0"

                    self._device["deviceSettings"]["brightnessValue"] = data["H2"]

            self.dispatch_state_timer()
            # self.dispatch_state_updated()

    def state_device_receive(self, device_state):
        """None."""
        # Safely copy appliance data if it exists
        if "appliance" in self._device:
            device_state["appliance"] = self._device["appliance"]
        self._device = device_state
        self.dispatch_state_timer()

    def dispatch_state_timer(self):
        """None."""
        with self._timer_lock:
            try:
                with contextlib.suppress(Exception):
                    self._timer_state_update.cancel()

                self._timer_state_update = Timer(1, self.dispatch_state_updated)
                self._timer_state_update.start()
            except Exception:
                _LOGGER.error(sys.exc_info()[1])

    def dispatch_state_updated(self):
        """None."""
        for listener in self.__event_listener:
            asyncio.run_coroutine_threadsafe(
                listener.state_updated(), self._api.hass.loop
            )

    def lost_connection(self):
        """None."""
        self._device["deviceStatus"] = 0
        self.dispatch_state_timer()
