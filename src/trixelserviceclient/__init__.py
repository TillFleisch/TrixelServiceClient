"""Simple client for the trixel based environmental observation sensor network."""

import asyncio
import importlib
import json
from datetime import datetime
from http import HTTPStatus
from typing import Callable

import httpx
import packaging.version
import pynyhtm
from pynyhtm import HTM
from trixellookupclient import Client as TLSClient
from trixellookupclient.api.trixel_information import (
    get_tms_which_manages_the_trixel_trixel_trixel_id_tms_get as get_tms_from_trixel,
)
from trixellookupclient.api.trixel_information import (
    get_trixel_sensor_count_trixel_trixel_id_sensor_count_get as get_sensor_count,
)
from trixellookupclient.models import TrixelManagementServer, TrixelMap
from trixellookupclient.types import Response as TLSResponse
from trixelmanagementclient import Client as TMSClient
from trixelmanagementclient.api.default import ping_ping_get as tms_get_ping
from trixelmanagementclient.api.measurement_station import (
    add_measurement_station_measurement_station_put as tms_update_station,
)
from trixelmanagementclient.api.measurement_station import (
    add_sensor_to_measurement_station_measurement_station_sensor_post as tms_add_sensor,
)
from trixelmanagementclient.api.measurement_station import (
    create_measurement_station_measurement_station_post as tms_register_station,
)
from trixelmanagementclient.api.measurement_station import (
    delete_measurement_station_measurement_station_delete as tms_delete_station,
)
from trixelmanagementclient.api.measurement_station import (
    delete_sensor_from_measurement_station_measurement_station_sensor_sensor_id_delete as tms_delete_sensor,
)
from trixelmanagementclient.api.measurement_station import (
    get_measurement_station_detail_measurement_station_get as tms_get_station_detail,
)
from trixelmanagementclient.api.measurement_station import (
    get_sensors_for_measurement_station_measurement_station_sensors_get as tms_get_sensors,
)
from trixelmanagementclient.api.trixels import (
    publish_sensor_updates_to_trixels_trixel_update_put as tms_batch_publish,
)
from trixelmanagementclient.models import Measurement as TMSMeasurement
from trixelmanagementclient.models import MeasurementStation, MeasurementStationCreate
from trixelmanagementclient.models import Ping as TMSPing
from trixelmanagementclient.models import (
    PublishSensorUpdatesToTrixelsTrixelUpdatePutUpdates as Update,
)
from trixelmanagementclient.models import SensorDetailed
from trixelmanagementclient.types import Response as TMSResponse

from .exception import (
    AuthenticationError,
    CriticalError,
    InvalidStateError,
    ServerError,
)
from .logging_helper import get_logger
from .schema import (
    ClientConfig,
    Coordinate,
    MeasurementStationConfig,
    MeasurementType,
    SeeOtherReason,
    Sensor,
    TMSInfo,
    TrixelLevelChange,
)

logger = get_logger(__name__)


def assert_valid_result(status_code: HTTPStatus, message: str, target_status_code: HTTPStatus = HTTPStatus.OK) -> None:
    """
    Assert that a request result matches the requirement and raise an appropriate exception otherwise.

    :param message: An error message which will be used for exceptions and logging.
    :param status_code: The status code which was returned by the server.
    :param target_status_code: The expected status code

    """
    if status_code >= HTTPStatus.INTERNAL_SERVER_ERROR and not status_code == target_status_code:
        logger.critical(f"{message}; status code: {status_code}")
        raise ServerError(message)
    elif (
        status_code == HTTPStatus.UNAUTHORIZED or status_code == HTTPStatus.FORBIDDEN
    ) and not status_code == target_status_code:
        logger.critical(f"{message}; status code: {status_code}")
        raise AuthenticationError(message)
    elif status_code != target_status_code:
        logger.critical(f"{message}; status code: {status_code} - {type(status_code)}")
        raise CriticalError(message)


class Client:
    """Simple client which manages multiple sensors, negotiates appropriate trixels and publishes updates to a TMS."""

    # lookup server client reference
    _tsl_client: TLSClient

    # trixel_id -> (tms_id, TMSClient)
    _tms_lookup: dict[int, TMSInfo]

    # Lookup table which yields the trixel to which different measurement types contribute
    _trixel_lookup: dict[MeasurementType, int]

    # The configuration used by this client
    _config: ClientConfig

    # Indicates if the client is in-sync with the responsible TMS
    _ready: asyncio.Event

    # Indicates that the client has finished it's work when set
    _dead: asyncio.Event

    # User defined method which is called to persist configuration changes
    _config_persister: Callable[[ClientConfig], None]

    @property
    def location(self) -> Coordinate:
        """Location property getter."""
        return self._config.location

    async def set_location(self, location: Coordinate) -> bool:
        """
        Location setter which automatically triggers a trixel ID re-negotiation.

        :param location: the new location
        :returns: True if synchronization with all TMSs was successful, False otherwise
        """
        logger.debug(f"Changing location to ({location})")
        old_location = self._config.location
        self._config.location = location
        try:
            if not self.is_dead.is_set() and self._ready.is_set():
                self._ready.clear()
                await self._tls_negotiate_trixel_ids()
                await self._update_responsible_tms()
                self._persist_config()
                self._ready.set()
            else:
                self._persist_config()
            return True
        except Exception:
            self._config.location = old_location
            return False

    @property
    def k(self) -> int:
        """K anonymity requirement property getter."""
        return self._config.k

    async def set_k(self, k: int) -> bool:
        """
        K anonymity requirement setter which synchronies with the TMS.

        :param k: the new k anonymity requirement
        :returns: True if synchronizations with all TMSs was successful, False otherwise
        """
        logger.debug(f"Changing k-requirement to {k}")
        old_k = self._config.k
        self._config.k = k
        try:
            if not self.is_dead.is_set() and self._ready.is_set():
                self._ready.clear()
                await self._sync_all_tms()
                self._persist_config()
                self._ready.set()
            else:
                self._persist_config()
            return True
        except Exception:
            self._config.k = old_k
            return False

    @property
    def sensors(self) -> list[Sensor]:
        """List of sensors managed by this client."""
        return self._config.sensors

    def get_tms(self) -> TMSInfo:
        """
        Get the (single) TMS with which this client is associated.

        Note:
        As the current client implementation only supports a single TMS, the first registered TMS is retrieved.
        The underlying assumption is that each trixel and measurement type combination is managed by the same TMS.
        This may not be the case in a system with multiple TMSs.

        :returns: TMS details
        """
        if len(self._tms_lookup.values()) == 0:
            logger.critical("TMS unknown!")
            raise CriticalError("TMS unknown!")
        return next(iter(self._tms_lookup.values()))

    async def add_sensor(self, new_senor: Sensor) -> Sensor:
        """
        Add a new sensor to this client and sync with the responsible TMS.

        :param new_sensor: The sensor which should be added
        :returns: The added sensor which contains it's assigned ID
        """
        if self.is_dead.is_set() or not self._ready.is_set():
            raise InvalidStateError("Sensors can only be added when the client is running and ready!")

        if new_senor.sensor_id is not None:
            raise ValueError("Sensor must have undefined ID!")

        new_sensor_index = len(self._config.sensors)
        self._config.sensors.append(new_senor)

        # Determine trixels including a potentially newly added type
        # Register at new TMSs
        await self._tls_negotiate_trixel_ids()
        await self._update_responsible_tms()
        await self._sync_all_tms()

        tms = self.get_tms()

        self._ready.clear()
        try:
            await self._sync_sensors(tms)
            self._persist_config()
            self._ready.set()
        except Exception:
            logger.warning(f"Failed to add sensor: {new_senor.sensor_id}!")
            self._config.sensors.pop()
            raise

        return self._config.sensors[new_sensor_index]

    async def update_sensor_details(self, new_sensor: Sensor) -> Sensor:
        """
        Update a sensors details within the client and sync change to the TMS.

        Note: currently - at the TMS the sensor is delete and newly instantiated

        :param new_sensor: The new sensor configuration (must contain an ID)
        :returns: The updated sensor (which currently may have a different ID)
        """
        if new_sensor.sensor_id is None:
            raise ValueError("Sensor does not provide an id.")

        sensor_index = None
        for idx, existing_sensor in enumerate(self._config.sensors):
            if existing_sensor.sensor_id == new_sensor.sensor_id:
                sensor_index = idx

        if sensor_index is None:
            raise ValueError("Invalid sensor provided, ID not found.")

        old_sensor = self._config.sensors[sensor_index]
        self._config.sensors[sensor_index] = new_sensor

        if not self.is_dead.is_set() and self._ready.is_set():
            tms = self.get_tms()
            self._ready.clear()
            try:
                await self._sync_sensors(tms)
                self._persist_config()
                self._ready.set()
            except Exception:
                logger.warning(f"Failed to update sensor: {new_sensor.sensor_id}!")
                self._config.sensors[sensor_index] = old_sensor
                raise
        else:
            self._persist_config()

        logger.debug(f"Updated sensor {new_sensor.sensor_id}")
        return self._config.sensors[sensor_index]

    async def delete_sensor(self, sensor: int | Sensor):
        """
        Delete a sensor from this client and sync the change the the TMS.

        :param sensor: The Sensor (or it's id) which should be removed
        """
        if isinstance(sensor, Sensor):
            sensor_id = sensor.sensor_id
        else:
            sensor_id = sensor

        sensor_index = None
        for idx, existing_sensor in enumerate(self._config.sensors):
            if existing_sensor.sensor_id == sensor_id:
                sensor_index = idx

        if sensor_index is None:
            raise ValueError("Invalid sensor provided.")

        old_sensor = self._config.sensors[sensor_index]
        del self._config.sensors[sensor_index]

        if not self.is_dead.is_set() and self._ready.is_set():
            tms = self.get_tms()
            self._ready.clear()
            try:
                await self._sync_sensors(tms)
                self._persist_config()
                self._ready.set()
            except Exception:
                logger.warning(f"Failed to delete sensor {old_sensor.sensor_id}!")
                self._config.sensors.append(old_sensor)
                raise
        else:
            self._persist_config()

    @property
    def is_ready(self) -> asyncio.Event:
        """
        Get the ready state of this Client.

        :returns: event which when set indicates that the client is ready and in-sync with the responsible TMS
        """
        return self._ready

    @property
    def is_dead(self) -> asyncio.Event:
        """
        Get the running state of this client.

        :returns: event which when set indicates that the client is running
        """
        return self._dead

    def __init__(self, config: ClientConfig, config_persister: Callable[[ClientConfig], None] | None):
        """Initialize the client with the given config."""
        self._config = config
        self._config_persister = config_persister
        self._tms_lookup = dict()
        self._trixel_lookup = dict()
        self._ready = asyncio.Event()
        self._dead = asyncio.Event()

        tls_api_version = importlib.metadata.version("trixellookupclient")
        tls_major_version = packaging.version.Version(tls_api_version).major
        self._tsl_client = TLSClient(
            base_url=f"http{'s' if self._config.tls_use_ssl else ''}://{config.tls_host}/v{tls_major_version}",
            timeout=httpx.Timeout(self._config.client_timeout),
        )

    def _persist_config(self):
        """Call the user defined configuration persist method."""
        if self._config_persister is not None:
            logger.debug("Persisting client configuration.")
            self._config_persister(self._config)

    async def start(self):
        """Start the client, registers or resumes work at the responsible TMS."""
        self._dead.clear()

        await self._tls_negotiate_trixel_ids()
        await self._update_responsible_tms()
        await self._sync_all_tms()

        self._ready.set()

    def kill(self) -> None:
        """Set the dead lock to gracefully kill this client and other related entities."""
        self._ready.clear()
        self._dead.set()

    async def _tls_negotiate_trixel_ids(self):
        """Negotiate the smallest trixels for each measurement type which satisfies the k requirement."""
        types = set()

        if len(self._config.sensors) == 0:
            raise CriticalError("Cannot determine TMS without sensor configuration.")

        for sensor in self._config.sensors:
            types.add(sensor.measurement_type)

        if len(types) == 0:
            self._trixel_lookup = dict()
            return

        sc = pynyhtm.SphericalCoordinate(self._config.location.latitude, self._config.location.longitude)

        trixels: dict[MeasurementType, int] = dict()

        for type_ in types:
            trixels[type_] = sc.get_htm_id(level=1)

        # Descend as deep as the user config allows
        for level in range(1, self._config.max_depth):
            trixel_id = sc.get_htm_id(level=level)

            trixel_info: TLSResponse[TrixelMap] = await get_sensor_count.asyncio_detailed(
                client=self._tsl_client, trixel_id=trixel_id, types=types
            )

            assert_valid_result(
                message=f"Failed to negotiate trixel IDs. - {trixel_info.content}", status_code=trixel_info.status_code
            )

            trixel_info: TrixelMap = trixel_info.parsed

            empty = True
            for type_ in trixel_info.sensor_counts.to_dict():
                if trixel_info.sensor_counts[type_] >= self._config.k:
                    empty = False
                    # Use one trixel lower than k-anonymous, assume the TMS retains this information
                    trixels[type_] = sc.get_htm_id(level=level + 1)

            if empty:
                break

        for type_, trixel_id in trixels.items():
            logger.info(
                f"Retrieved trixel (id: {trixel_id} level: {HTM.get_level(trixel_id)}) for measurement type {type_}"
            )

        self._trixel_lookup = trixels

    async def _update_responsible_tms(self):
        """Retrieve the responsible TMSs for all required trixels."""
        tms_api_version = importlib.metadata.version("trixelmanagementclient")
        tms_ssl = "s" if self._config.tms_use_ssl else ""
        tms_major_version = packaging.version.Version(tms_api_version).major

        # Retrieve TMS for each trixel to which this client contributes
        for trixel_id in self._trixel_lookup.values():
            tms_response: TLSResponse[TrixelManagementServer] = await get_tms_from_trixel.asyncio_detailed(
                client=self._tsl_client, trixel_id=trixel_id
            )

            assert_valid_result(
                message=f"Failed to retrieve TMS responsible for trixel {trixel_id}): - {tms_response.content}",
                status_code=tms_response.status_code,
            )

            tms = tms_response.parsed

            tms_ids = set([x.id for x in self._tms_lookup.values()])
            if len(tms_ids) > 0 and tms.id not in tms_ids:
                raise CriticalError("Only single TMS supported!")

            if self._config.tms_address_override is not None:
                client = TMSClient(
                    base_url=f"http{tms_ssl}://{self._config.tms_address_override}/v{tms_major_version}",
                    timeout=httpx.Timeout(self._config.client_timeout),
                )
            else:
                client = TMSClient(
                    base_url=f"http{tms_ssl}://{tms.host}/v{tms_major_version}",
                    timeout=httpx.Timeout(self._config.client_timeout),
                )
            self._tms_lookup[trixel_id] = TMSInfo(id=tms.id, client=client, host=tms.host)

        if len(self._tms_lookup.values()) == 0:
            logger.critical("TMS unknown!")
            raise CriticalError("TMS unknown!")

        # Validate retrieved TMSs are available
        checked_tms_ids = set()
        for trixel_id, tms_info in self._tms_lookup.items():

            if tms_info.id in checked_tms_ids:
                continue
            checked_tms_ids.add(tms_info.id)

            tms_ping: TMSResponse[TMSPing] = await tms_get_ping.asyncio_detailed(client=tms_info.client)
            assert_valid_result(
                message=f"Failed to ping TMS(id:{tms_info.id} host: {tms_info.host}): {tms_ping.content}",
                status_code=tms_ping.status_code,
            )

            logger.info(f"Retrieved valid TMS(id:{tms_info.id} host: {tms_info.host}).")

    async def _register_at_tms(self, tms: TMSInfo) -> MeasurementStationConfig:
        """
        Register this client at the TMS.

        :param tms: TMS at which this client should register
        :returns: Measurement station details containing the uuid and the authentication token
        """
        register_response: TMSResponse[MeasurementStationCreate] = await tms_register_station.asyncio_detailed(
            client=tms.client, k_requirement=self._config.k
        )

        assert_valid_result(
            message=f"Failed to register at TMS. - {register_response.content}",
            status_code=register_response.status_code,
            target_status_code=HTTPStatus.CREATED,
        )

        register_response: MeasurementStationCreate = register_response.parsed

        if register_response.k_requirement != self._config.k:
            logger.critical("TMS not using desired k-requirement.")
            raise CriticalError("TMS not using desired k-requirement.")

        return MeasurementStationConfig(uuid=register_response.uuid, token=register_response.token)

    async def delete(self):
        """Remove this measurement station from all TMS where it's registered."""
        if self.is_dead.is_set() or not self._ready.is_set():
            raise InvalidStateError("Deletion only allowed if the client is running and ready!")

        tms = self.get_tms()

        delete_response: TMSResponse = await tms_delete_station.asyncio_detailed(
            client=tms.client, token=self._config.ms_config.token
        )

        assert_valid_result(
            message=f"Failed to delete measurement station at TMS: {tms.id}",
            status_code=delete_response.status_code,
            target_status_code=HTTPStatus.NO_CONTENT,
        )

        self._config.ms_config = None
        self._config.sensors = list()
        self._persist_config()
        logger.info(f"Removed measurement station from TMS {tms.id}.")
        self._dead.set()

    async def _sync_all_tms(self):
        """Synchronize this client with all TMSs."""
        tms = self.get_tms()
        await self.sync_with_tms(tms=tms)

    async def sync_with_tms(self, tms: TMSInfo):
        """Synchronize this client with the desired TMS."""
        ms_config = self._config.ms_config

        if ms_config is None:
            self._config.ms_config = await self._register_at_tms(tms)
            self._persist_config()

        await self._sync_station_properties(tms)
        await self._sync_sensors(tms)

        logger.info(f"Synchronized with TMS {tms.id}")

    async def _sync_station_properties(self, tms: TMSInfo):
        """
        Synchronize measurement station properties with the provided TMS.

        :param tms: The TMS with which properties are synchronized
        """
        detail_response: TMSResponse[MeasurementStation] = await tms_get_station_detail.asyncio_detailed(
            client=tms.client,
            token=self._config.ms_config.token,
        )

        assert_valid_result(
            message=f"Failed to fetch details from TMS: {tms.id}", status_code=detail_response.status_code
        )

        detail_response: MeasurementStation = detail_response.parsed

        if detail_response.k_requirement != self._config.k:
            update_response: TMSResponse[MeasurementStation] = await tms_update_station.asyncio_detailed(
                client=tms.client, token=self._config.ms_config.token, k_requirement=self._config.k
            )

            assert_valid_result(
                message=f"Failed to synchronize settings with TMS: {tms.id}", status_code=update_response.status_code
            )
            if update_response.parsed.k_requirement != self._config.k:
                logger.critical(f"Failed to synchronize settings with TMS: {tms.id}")
                raise ServerError(f"Failed to synchronize settings with TMS: {tms.id}")

    async def _sync_sensors(self, tms: TMSInfo):
        """
        Synchronize sensors and their properties with the provided TMS.

        :param tms: The TMS with which properties are synchronized
        """
        sensors_response: TMSResponse[list[SensorDetailed]] = await tms_get_sensors.asyncio_detailed(
            client=tms.client, token=self._config.ms_config.token
        )

        assert_valid_result(
            message=f"Failed to retrieve registered sensors from TMS: {tms.id}",
            status_code=sensors_response.status_code,
        )

        existing_sensors: list[SensorDetailed] = sensors_response.parsed

        missing_sensor_indices = set()
        update_sensor_indices = set()
        for idx, sensor in enumerate(self._config.sensors):
            if sensor.sensor_id is None:
                missing_sensor_indices.add(idx)
            else:
                for existing_sensor in existing_sensors:
                    if existing_sensor.id == sensor.sensor_id and (
                        existing_sensor.details.accuracy != sensor.accuracy
                        or existing_sensor.details.sensor_name != sensor.sensor_name
                        or existing_sensor.measurement_type != sensor.measurement_type
                    ):
                        update_sensor_indices.add(idx)

        # Find orphaned sensors
        delete_sensor_indices = set()
        for existing_sensor in existing_sensors:
            in_local_config = False
            for idx, sensor in enumerate(self._config.sensors):
                if sensor.sensor_id is not None and sensor.sensor_id == existing_sensor.id:
                    in_local_config = True
            if not in_local_config:
                delete_sensor_indices.add(existing_sensor.id)

        # Delete orphaned sensors form the TMS
        for sensor_id in delete_sensor_indices:
            logger.debug(f"Deleting orphaned sensor {sensor_id}")
            await self._tms_delete_sensor(tms, sensor_id)

        # Update existing sensors if there are config changes on the client side
        for idx in update_sensor_indices:
            # TODO: update sensor config at the TMS (not implemented)
            logger.warn("Replacing sensor due to configuration change!")
            sensor = self._config.sensors[idx]
            await self._tms_delete_sensor(tms, sensor.sensor_id)
            self._config.sensors[idx] = await self._tms_add_sensor(tms, sensor)
            self._persist_config()

        # Add new sensors to TMS
        for idx in missing_sensor_indices:
            self._config.sensors[idx] = await self._tms_add_sensor(tms, sensor)
            self._persist_config()

    async def _tms_add_sensor(self, tms: TMSInfo, sensor: Sensor) -> Sensor:
        """
        Create a new sensor at the TMS.

        :param tms: The TMS at which the sensor is added
        :param sensor: sensor with details, which will be added
        :returns: The added sensors with it's ID
        """
        add_sensor_response: TMSResponse[SensorDetailed] = await tms_add_sensor.asyncio_detailed(
            client=tms.client,
            token=self._config.ms_config.token,
            type=sensor.measurement_type,
            accuracy=sensor.accuracy,
            sensor_name=sensor.sensor_name,
        )

        assert_valid_result(
            message=f"Failed to retrieve registered sensors from TMS: {tms.id}",
            status_code=add_sensor_response.status_code,
            target_status_code=HTTPStatus.CREATED,
        )

        new_sensor: SensorDetailed = add_sensor_response.parsed
        logger.info(f"Added new sensor ({new_sensor.id}) to TMS {tms.id}.")

        return Sensor(
            sensor_id=new_sensor.id,
            measurement_type=new_sensor.measurement_type,
            accuracy=new_sensor.details.accuracy,
            sensor_name=new_sensor.details.sensor_name,
        )

    async def _tms_delete_sensor(self, tms: TMSInfo, sensor_id: int) -> None:
        """
        Delete the give sensor from the TMS.

        :param tms: The TMS at which the sensor should be deleted
        :param sensor_id: The id of the sensor which should be removed
        """
        delete_sensor_response: TMSResponse = await tms_delete_sensor.asyncio_detailed(
            client=tms.client, token=self._config.ms_config.token, sensor_id=sensor_id
        )

        assert_valid_result(
            message=f"Failed to delete sensor ({sensor_id}) from TMS: {tms.id}",
            status_code=delete_sensor_response.status_code,
            target_status_code=HTTPStatus.NO_CONTENT,
        )

        logger.info(f"Deleted sensor ({sensor_id}) from TMS: {tms.id}")

    def _should_renegotiate(self, sensors: dict[str, str]) -> bool:
        """
        Determine if this client should re-negotiate target trixel IDs after measurements submission.

        Ignores recommended trixel re-negotiation, if the max depth has been reached.

        :param sensors: dictionary containing the recommended level change direction for affected sensors
        :returns: True if target trixels should be re-negotiated
        """
        for sensor_id, level_change_str in sensors.items():
            if level_change_str == TrixelLevelChange.INCREASE:

                target_trixel: int | None = None
                for sensor in self._config.sensors:
                    if sensor.sensor_id == int(sensor_id):
                        target_trixel = self._trixel_lookup[sensor.measurement_type]

                if target_trixel is None or HTM.get_level(target_trixel) < self._config.max_depth:
                    return True
            if level_change_str == TrixelLevelChange.DECREASE:
                return True
        return False

    async def publish_values(self, updates: dict[int, tuple[datetime, float]]) -> None:
        """
        Publish measurements to the appropriate trixels and TMSs.

        :param updates: dictionary containing the (timestamp,value) for each sensor
        """
        if self.is_dead.is_set() or not self._ready.is_set():
            raise InvalidStateError("Updates only allowed if the client is running and ready!")

        tms = self.get_tms()

        batch_update: Update = Update()
        for sensor_id, (timestamp, value) in updates.items():

            config_sensor = None
            for sensor in self._config.sensors:
                if sensor.sensor_id == sensor_id:
                    config_sensor = sensor

            if config_sensor is None:
                raise ValueError(f"Invalid sensor id provided {sensor_id}")

            measurement = TMSMeasurement(sensor_id=sensor_id, timestamp=timestamp, value=value)

            trixel_id = self._trixel_lookup[sensor.measurement_type]
            if trixel_id not in batch_update:
                batch_update[trixel_id] = list()
            batch_update[trixel_id].append(measurement)

        publish_response: TMSResponse = await tms_batch_publish.asyncio_detailed(
            client=tms.client, token=self._config.ms_config.token, body=batch_update
        )

        if publish_response.status_code == HTTPStatus.SEE_OTHER:
            try:
                content = json.loads(publish_response.content)
                if content["reason"] == SeeOtherReason.CHANGE_TRIXEL:
                    if self._should_renegotiate(content["sensors"]):
                        logger.info("Renegotiating Trixel IDs due to TMS recommendation.")
                        await self._tls_negotiate_trixel_ids()
                    return
                elif content["reason"] == SeeOtherReason.WRONG_TMS:
                    logger.critical("TMS migration required, but not supported.")
                    raise NotImplementedError("TMS migration required, but not supported.")
            except json.JSONDecodeError:
                logger.warning("Failed to parse SEE_OTHER response.")
                return
        if publish_response.status_code == HTTPStatus.NOT_FOUND:
            logger.critical("Invalid sensor ID sent to TMS. Synchronization required.")
            raise NotImplementedError("Invalid sensor ID sent to TMS. Synchronization required.")
        assert_valid_result(
            message=f"Failed to publish values to TMS: {tms.id} - {publish_response}",
            status_code=publish_response.status_code,
        )  # TODO: remove modificution
