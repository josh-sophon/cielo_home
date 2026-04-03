"""None."""

import asyncio
from collections.abc import Awaitable
import contextlib
import copy
from datetime import datetime
import json
import logging
import re
import sys
from threading import Lock, Timer

import aiohttp
from aiohttp import ClientSession, ClientWebSocketResponse, WSMsgType

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import URL_API, URL_API_WSS, URL_CIELO, USER_AGENT

_LOGGER = logging.getLogger(__name__)

TIMEOUT_RECONNECT = 10
TIME_REFRESH_TOKEN = 3300
TIMER_PING = 540
TIMER_PONG = 60

# TIME_REFRESH_TOKEN = 20
# TIMER_PING = 100


class CieloHome:
    """Set up Cielo Home api."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Set up Cielo Home api."""
        self.can_reload: bool = True
        self._is_running: bool = True
        self._stop_running: bool = False
        self._access_token: str = ""
        self._refresh_token: str = ""
        self._session_id: str = ""
        self._user_id: str = ""
        # self._user_name: str = ""
        # self._password: str = ""
        self._headers: dict[str, str] = {}
        self._websocket: ClientWebSocketResponse
        self._ws_session: ClientSession
        self.__event_listener: list[object] = []
        self._msg_to_send: list[object] = []
        self._msg_lock = Lock()
        self._timer_connection_lost: Timer = None
        self._last_refresh_token_ts: int
        self._token_expire_in_ts: int
        self._last_ts_msg: int = 0
        self._last_ts_ping: int = 0
        self._last_ts_pong: int = 0
        self._last_connection_ts: int = 0
        self._last_x_api_key: str = None
        self._reconnect_now = False
        self.hass: HomeAssistant = hass
        self._entry: ConfigEntry = entry
        self._appliance_id = None
        self.background_tasks_wss = set()
        self._headers = {
            "content-type": "application/json; charset=UTF-8",
            "referer": URL_CIELO,
            "origin": URL_CIELO,
            "user-agent": USER_AGENT,
            "host": URL_API,
        }

    async def close(self):
        """None."""
        self._stop_running = True
        self._is_running = False
        await asyncio.sleep(0.5)

    def add_listener(self, listener: object):
        """None."""
        self.__event_listener.append(listener)

    async def async_auth(
        self,
        access_token: str,
        refresh_token: str,
        session_id: str,
        user_id: str,
        x_api_key: str,
    ) -> bool:
        """Set up Cielo Home auth."""

        self._access_token = access_token
        self._refresh_token = refresh_token
        self._session_id = session_id
        self._user_id = user_id
        self._last_x_api_key = x_api_key

        self._last_refresh_token_ts = self.get_ts()
        self._token_expire_in_ts = self.get_ts() + TIME_REFRESH_TOKEN

        await self.async_refresh_token(test=True)

        if self._access_token != "":
            self.create_websocket_log_exception(False)

        return True

    async def async_refresh_token(
        self,
        access_token: str = "",
        refresh_token: str = "",
        session_id: str = "",
        user_id: str = "",
        x_api_key: str = "",
        test: bool = False,
    ) -> bool:
        """Set up Cielo Home refresh."""
        _LOGGER.debug("Call refreshToken %s", x_api_key)

        # Opening JSON file
        # fullpath: str = str(pathlib.Path(__file__).parent.resolve()) + "/login.json"
        # file = open(fullpath)

        # # returns JSON object as
        # # a dictionary
        # data = json.load(file)
        # # Iterating through the json
        # # list
        # access_token = data["data"]["user"]["accessToken"]
        # refresh_token = data["data"]["user"]["refreshToken"]
        # self._session_id = data["data"]["user"]["sessionId"]
        # file.close()
        try:
            if test:
                self._last_refresh_token_ts = self.get_ts()
                self._token_expire_in_ts = self.get_ts() + TIME_REFRESH_TOKEN

            if access_token != "":
                self._access_token = access_token
                self._refresh_token = refresh_token
                self._session_id = session_id
                self._user_id = user_id
                self._last_x_api_key = x_api_key

            headers: dict[str, str] = self._headers
            headers["authorization"] = self._access_token
            headers["x-api-key"] = self._last_x_api_key

            data = {
                "local": "en",
                "refreshToken": self._refresh_token,
            }
            async with ClientSession() as session:  # noqa: SIM117
                async with session.post(
                    "https://" + URL_API + "/web/token/refresh",
                    headers=self._headers,
                    json=data,
                ) as response:
                    if response.status == 200:
                        repjson = await response.json()
                        if repjson["status"] == 200 and repjson["message"] == "SUCCESS":
                            self._access_token = repjson["data"]["accessToken"]
                            self._refresh_token = repjson["data"]["refreshToken"]
                            expire: int = int(repjson["data"]["expiresIn"]) - 300
                            if (
                                expire < self._token_expire_in_ts
                                or expire < self.get_ts()
                            ):
                                self._token_expire_in_ts = (
                                    self.get_ts() + TIME_REFRESH_TOKEN
                                )
                            else:
                                self._token_expire_in_ts = expire

                            if not test:
                                if self._entry is not None:
                                    config_data = self._entry.data.copy()
                                    config_data["access_token"] = self._access_token
                                    config_data["refresh_token"] = self._refresh_token
                                    self.can_reload = False
                                    self.hass.config_entries.async_update_entry(
                                        self._entry, data=config_data
                                    )
                                _LOGGER.debug("Call refreshToken success")
                                await self._ws_session.close()
                                self._last_refresh_token_ts = self.get_ts()
                            else:
                                _LOGGER.debug("Call test refreshToken success")
                            return True
                    else:
                        _LOGGER.error("Call refreshToken error %s", response.status)
        except Exception:
            _LOGGER.error(sys.exc_info()[1])

        return False

    async def async_connect_wss(self, update_state: bool = False):
        """None."""
        headers_wss = {
            "Host": URL_API_WSS,
            "Cache-control": "no-cache",
            "Pragma": "no-cache",
            "User-agent": USER_AGENT,
        }

        self._reconnect_now = False
        wss_uri = "wss://" + URL_API_WSS + "/websocket/"

        self._is_running = True
        self._stop_running = False
        try:
            async with ClientSession() as ws_session:
                self._ws_session = ws_session
                async with ws_session.ws_connect(
                    wss_uri,
                    headers=headers_wss,
                    params={
                        "sessionId": self._session_id,
                        "token": self._access_token,
                    },
                    origin=URL_CIELO[:-1],
                    compress=15,
                    autoping=False,
                ) as websocket:
                    self._websocket = websocket
                    if self._last_ts_ping == 0:
                        self._last_ts_ping = self.get_ts()
                        self._last_ts_pong = self._last_ts_ping + TIMER_PONG

                    _LOGGER.info("Connected success")
                    self._last_connection_ts = self.get_ts()
                    self.stop_timer_connection_lost()

                    if update_state:
                        self.create_task_log_exception(
                            self.update_state_device(), False
                        )
                    # self.start_timer_ping()
                    while self._is_running:
                        now: int = self.get_ts()

                        try:
                            msg = await self._websocket.receive(timeout=0.1)
                            if msg.type in (
                                WSMsgType.CLOSE,
                                WSMsgType.CLOSED,
                                WSMsgType.CLOSING,
                                WSMsgType.ERROR,
                            ):
                                # self._timer_ping.cancel()
                                _LOGGER.debug("Websocket closed : %s", msg.type)
                                if (now - self._last_connection_ts) > TIMEOUT_RECONNECT:
                                    self._reconnect_now = True
                                break

                            try:
                                js_data = json.loads(msg.data)
                                if _LOGGER.isEnabledFor(logging.DEBUG):
                                    debug_data = copy.copy(js_data)
                                    if hasattr(debug_data, "accessToken"):
                                        debug_data["accessToken"] = "*****"
                                    if hasattr(debug_data, "refreshToken"):
                                        debug_data["refreshToken"] = "*****"
                                    _LOGGER.debug(
                                        "Receive Json : %s", json.dumps(debug_data)
                                    )

                                with contextlib.suppress(Exception):
                                    if js_data["message"] == "Internal server error":
                                        self._last_ts_pong = self.get_ts() + 1

                                with contextlib.suppress(Exception):
                                    if (
                                        js_data["message_type"] == "StateUpdate"
                                        or js_data["message_type"]
                                        == "DeviceSettingsAck"
                                    ):
                                        for listener in self.__event_listener:
                                            listener.data_receive(js_data)

                            except ValueError:
                                pass

                        except asyncio.exceptions.TimeoutError:
                            pass
                        except asyncio.exceptions.CancelledError:
                            pass

                        if now > (self._token_expire_in_ts):
                            self._reconnect_now = True
                            self._token_expire_in_ts = now + 60
                            self.create_task_log_exception(self.async_refresh_token())
                        elif now - self._last_ts_ping >= TIMER_PING:
                            self._last_ts_ping = now
                            self._last_ts_pong = now
                            self.send_json("ping")
                        elif (
                            now > (self._last_ts_pong + TIMER_PONG)
                        ) and self._last_ts_pong == self._last_ts_ping:
                            self._reconnect_now = True
                            self._is_running = False

                        msg: object = None
                        if len(self._msg_to_send) > 0:
                            with self._msg_lock:
                                try:
                                    while len(self._msg_to_send) > 0:
                                        msg = self._msg_to_send.pop(0)
                                        if msg == "ping":
                                            _LOGGER.debug("Send text : ping")
                                            await self._websocket.send_str(msg)
                                        else:
                                            if _LOGGER.isEnabledFor(logging.DEBUG):
                                                debug_data = copy.copy(msg)
                                                # debug_data["token"] = "*****"
                                                _LOGGER.debug(
                                                    "Send Json : %s",
                                                    json.dumps(debug_data),
                                                )

                                            await self._websocket.send_json(msg)

                                        msg = None
                                        await asyncio.sleep(0.1)
                                except Exception:
                                    _LOGGER.error("Failed to send Json")
                                    if msg is not None:
                                        self._msg_to_send.insert(0, msg)

        except Exception:
            _LOGGER.error(sys.exc_info()[1])

        if hasattr(self, "_ws_session") and not self._ws_session.closed:
            # self._timer_ping.cancel()
            await self._ws_session.close()

        if hasattr(self, "_websocket") and not self._websocket.closed:
            # self._timer_ping.cancel()
            await self._websocket.close()

        if not self._stop_running:
            # for listener in self.__event_listener:
            #    listener.lost_connection()
            self.start_timer_connection_lost()
            if not self._reconnect_now:
                _LOGGER.debug(
                    "Try reconnection in " + str(TIMEOUT_RECONNECT) + " secondes"
                )
                await asyncio.sleep(TIMEOUT_RECONNECT)
            else:
                _LOGGER.debug("Reconnection")
            self._last_ts_ping = 0
            await self.async_refresh_token()
            self.create_websocket_log_exception(True)

    def send_action(self, msg) -> None:
        """None."""
        # msg["token"] = self._access_token
        with contextlib.suppress(KeyError):
            if msg["mid"] == "":
                msg["mid"] = self._session_id

        msg["ts"] = self.get_ts()

        # to be sure each msg have different ts, when 2 msg are send quickly
        if msg["ts"] == self._last_ts_msg:
            msg["ts"] = msg["ts"] + 1

        self._last_ts_msg = msg["ts"]

        self.send_json(msg)

    def start_timer_connection_lost(self):
        """None."""
        self._timer_connection_lost = Timer(
            TIMEOUT_RECONNECT + 2, self.dispatch_connection_lost
        )
        self._timer_connection_lost.start()  # Here run is called

    def stop_timer_connection_lost(self):
        """None."""
        if self._timer_connection_lost:
            self._timer_connection_lost.cancel()
            self._timer_connection_lost = None

    def dispatch_connection_lost(self):
        """None."""
        for listener in self.__event_listener:
            listener.lost_connection()

    def send_json(self, data):
        """None."""
        with self._msg_lock:
            try:
                self._msg_to_send.append(data)
            except Exception:
                _LOGGER.error(sys.exc_info()[1])

    def get_ts(self) -> int:
        """None."""
        return int(datetime.now().timestamp())
        # return int((datetime.utcnow() - datetime.fromtimestamp(0)).total_seconds())

    @staticmethod
    def _adapt_ct01_device(device):
        """Transform CT01 wired thermostat data into Breez-compatible format.

        CT01 devices use 'preferences' instead of 'latestAction' and have no
        applianceId (they control HVAC via wiring, not IR). This synthesises the
        fields that CieloHomeDevice expects so the rest of the code works
        unchanged.
        """
        prefs = device.get("preferences", {})
        manual = prefs.get("manualSettings", {})
        equip = device.get("equipmentSettings", {})

        # --- mode mapping (CT01 numeric → Breez string) ---
        _MODE_MAP = {0: "off", 1: "heat", 2: "cool", 3: "auto", 4: "heat"}
        mode_num = prefs.get("mode", 0)
        power = prefs.get("equipmentPower", "off")

        mode_str = _MODE_MAP.get(mode_num, "auto")
        if power == "off":
            # When off, keep previous mode for display
            mode_str = _MODE_MAP.get(prefs.get("previousMode", 3), "auto")

        # --- target temperature (depends on active mode) ---
        if mode_num == 2:
            target = manual.get("coolSetPoint", "73")
        elif mode_num in (1, 4):
            target = manual.get("heatSetPoint", "65")
        elif mode_num == 3:
            target = manual.get("autoCoolSetPoint", "73")
        else:
            target = manual.get("coolSetPoint", "73")

        target_str = str(target).replace(".0", "")

        # --- fan mapping (CT01: 0/2=auto, 1=on) ---
        fan_num = prefs.get("fan", 0)
        fan_str = "low" if fan_num == 1 else "auto"

        # --- synthesise latestAction ---
        device["latestAction"] = {
            "power": power,
            "mode": mode_str,
            "temp": target_str,
            "fanspeed": fan_str,
            "swing": "",
            "turbo": "off",
            "light": "off",
            "followme": "off",
            "preset": 0,
        }

        # --- synthesise appliance capabilities ---
        heat_range = prefs.get("heatLimit", "45:90").replace(".0", "")
        cool_range = prefs.get("coolLimit", "45:90").replace(".0", "")

        # Use the wider of the two ranges
        try:
            h_lo, h_hi = heat_range.split(":")
            c_lo, c_hi = cool_range.split(":")
            range_lo = min(int(float(h_lo)), int(float(c_lo)))
            range_hi = max(int(float(h_hi)), int(float(c_hi)))
            temp_range = f"{range_lo}:{range_hi}"
        except Exception:
            temp_range = "45:90"

        # Determine available modes from equipment type
        equip_type = equip.get("equipmentType", 1)
        if equip_type == 1:  # heat pump
            modes = "heat:cool:auto"
        elif equip_type == 2:  # conventional heat + cool
            modes = "heat:cool:auto"
        elif equip_type == 3:  # cool only
            modes = "cool"
        else:
            modes = "heat:cool:auto"

        device["appliance"] = {
            "applianceId": "CT01",
            "mode": modes,
            "fan": "auto:low" if fan_num in (0, 1, 2) else "",
            "temp": temp_range,
            "tempIncrement": 1,
            "isFaren": device.get("isFaren", 1),
            "swing": "",
            "turbo": "",
            "isDisplayLight": 0,
            "followme": "",
            "isFreezepointDisplay": 0,
            "isMultiModeTempRange": 0,
        }

        # --- fill in missing fields the device class expects ---
        device.setdefault("applianceId", "CT01")
        device.setdefault("applianceType", "THERMOSTAT")
        device.setdefault("connectionSource", 1)
        device.setdefault("myRuleConfiguration", {})
        device.setdefault("deviceSettings", {
            "screenDisplayValue": "",
            "brightnessValue": "",
            "idleScreenTimeout": "",
            "idleBrightnessValue": "",
        })

        _LOGGER.info(
            "CT01 thermostat '%s' adapted: power=%s mode=%s temp=%s",
            device.get("deviceName", "?"),
            power,
            mode_str,
            target_str,
        )
        return device

    async def async_get_devices(self):
        """None."""
        devices = await self.async_get_thermostats()
        devicesNotSupported = []

        appliance_ids = ""
        unique_appliance_ids = set()  # Use a set to track unique appliance IDs

        if devices is not None:
            for device in devices:
                appliance_id: str = str(device.get("applianceId", ""))
                if appliance_id in ("0", ""):
                    # Check if this is a CT01 wired thermostat
                    if device.get("deviceType") == "THERMOSTAT" or device.get("deviceTypeVersion", "").startswith("CT"):
                        self._adapt_ct01_device(device)
                        _LOGGER.info(
                            "CT01 device '%s' included via adapter",
                            device.get("deviceName", "Unknown"),
                        )
                    else:
                        devicesNotSupported.append(device)
                    continue

                # Only add to appliance_ids string if we haven't seen this appliance ID before
                if appliance_id not in unique_appliance_ids:
                    unique_appliance_ids.add(appliance_id)
                    if appliance_ids != "":
                        appliance_ids = appliance_ids + ","
                    appliance_ids = appliance_ids + str(appliance_id)

            # Fetch appliance data for Breez devices (CT01 already has synthetic data)
            if appliance_ids:
                appliances = await self.async_get_thermostat_info(appliance_ids)
            else:
                appliances = []

            if len(devicesNotSupported) > 0:
                for device in devicesNotSupported:
                    _LOGGER.warning(
                        "Device '"
                        + str(device["deviceName"])
                        + "' not supported, invalid appliance '"
                        + str(device.get("applianceId", ""))
                        + "'"
                    )
                    devices.remove(device)

            # Attach appliance data to ALL devices, even those sharing the same appliance ID
            for device in devices:
                # Skip CT01 devices — they already have synthetic appliance data
                if "appliance" in device:
                    continue

                device_appliance_id = device["applianceId"]
                appliance_attached = False
                for appliance in appliances:
                    if appliance["applianceId"] == device_appliance_id:
                        device["appliance"] = appliance
                        appliance_attached = True
                        break

                # If no appliance data was found, log a warning
                if not appliance_attached:
                    _LOGGER.warning(
                        "No appliance data found for device '%s' with appliance ID %s",
                        device.get("deviceName", "Unknown"),
                        device_appliance_id,
                    )

            return devices

        return []

    async def update_state_device(self):
        """None."""
        devices = await self.async_get_thermostats()
        for listener in self.__event_listener:
            for device in devices:
                if device["macAddress"] == listener.get_mac_address():
                    # Re-adapt CT01 devices on state refresh
                    is_ct01 = (
                        device.get("deviceType") == "THERMOSTAT"
                        or device.get("deviceTypeVersion", "").startswith("CT")
                    )
                    if is_ct01 and "latestAction" not in device:
                        self._adapt_ct01_device(device)
                    listener.state_device_receive(device)

    async def async_get_thermostats(self):
        """Get de the list Devices/Thermostats."""
        if self._last_x_api_key is None:
            await self.async_refresh_token()

        self._headers["authorization"] = self._access_token
        self._headers["x-api-key"] = self._last_x_api_key
        devices = None

        # Retry logic for API calls
        max_retries = 3
        for attempt in range(max_retries):
            try:
                async with ClientSession(
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as session:
                    async with session.get(
                        "https://" + URL_API + "/web/devices?limit=420",
                        headers=self._headers,
                    ) as response:
                        if response.status == 200:
                            repjson = await response.json()
                            if (
                                repjson["status"] == 200
                                and repjson["message"] == "SUCCESS"
                            ):
                                devices = repjson["data"]["listDevices"]
                                if _LOGGER.isEnabledFor(logging.DEBUG):
                                    _LOGGER.debug("devices : %s", json.dumps(devices))
                                break
                            else:
                                _LOGGER.warning(
                                    f"API returned error: {repjson.get('message', 'Unknown error')}"
                                )
                        elif response.status == 401:
                            _LOGGER.info("Unauthorized, refreshing token...")
                            if await self.async_refresh_token():
                                self._headers["authorization"] = self._access_token
                                continue
                            else:
                                _LOGGER.error("Failed to refresh token")
                                break
                        else:
                            _LOGGER.warning(
                                f"HTTP {response.status}: {await response.text()}"
                            )

            except asyncio.TimeoutError:
                _LOGGER.warning(f"Timeout on attempt {attempt + 1}/{max_retries}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2**attempt)  # Exponential backoff
            except Exception as e:
                _LOGGER.error(
                    f"Error getting devices on attempt {attempt + 1}/{max_retries}: {e}"
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(2**attempt)  # Exponential backoff

        devices_supported: list = []

        if devices is not None:
            for device in devices:
                try:
                    appliance_id: str = str(device.get("applianceId", ""))
                    is_ct01 = (
                        device.get("deviceType") == "THERMOSTAT"
                        or device.get("deviceTypeVersion", "").startswith("CT")
                    )
                    if (appliance_id and appliance_id != "0") or is_ct01:
                        devices_supported.append(device)
                    else:
                        _LOGGER.warning(
                            f"Device {device.get('deviceName', 'Unknown')} has invalid appliance ID: {appliance_id}"
                        )
                except Exception as e:
                    _LOGGER.error(f"Error processing device: {e}")
                    continue

        return devices_supported

    async def async_get_thermostat_info(self, appliance_ids):
        """Get de the list Devices/Thermostats."""
        # https://api.smartcielo.com/web/sync/db/6?applianceIdList=[1674]
        self._headers["authorization"] = self._access_token
        self._headers["x-api-key"] = self._last_x_api_key
        async with ClientSession() as session:  # noqa: SIM117
            async with session.get(
                "https://"
                + URL_API
                + "/web/sync/db/6?applianceIdList=["
                + appliance_ids
                + "]",
                headers=self._headers,
            ) as response:
                if response.status == 200:
                    repjson = await response.json()
                    if repjson["status"] == 200 and repjson["message"] == "SUCCESS":
                        appliances = repjson["data"]["listAppliances"]
                        if _LOGGER.isEnabledFor(logging.DEBUG):
                            _LOGGER.debug("appliances : %s", json.dumps(appliances))
                        return appliances
                else:
                    pass
        return []

    def create_websocket_log_exception(self, update_state: bool = True) -> asyncio.Task:
        """None."""
        task = asyncio.create_task(_log_exception(self.async_connect_wss(update_state)))
        self.background_tasks_wss.add(task)
        task.add_done_callback(self.call_back_task)
        return task

    def call_back_task(self, task: asyncio.Task):
        """None."""
        self.background_tasks_wss.remove(task)

    def create_task_log_exception(
        self, awaitable: Awaitable, long_running: bool = True
    ) -> asyncio.Task:
        """None."""

        task = asyncio.create_task(_log_exception(awaitable))
        if long_running:
            self.background_tasks_wss.add(task)
            task.add_done_callback(self.call_back_task)
        return task


async def _log_exception(awaitable):
    try:
        return await awaitable
    except Exception as e:
        _LOGGER.exception(e)
