"""Simple client for the trixel based environmental observation sensor network."""

import asyncio
import enum
import importlib
from http import HTTPStatus
from typing import Callable

import packaging.version
import pynyhtm
from pydantic import PositiveInt
from pydantic_extra_types.coordinate import Coordinate
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
    create_measurement_station_measurement_station_post as tms_register_station,
)
from trixelmanagementclient.api.measurement_station import (
    delete_measurement_station_measurement_station_delete as tms_delete_station,
)
from trixelmanagementclient.api.measurement_station import (
    get_measurement_station_detail_measurement_station_get as tms_get_station_detail,
)
from trixelmanagementclient.models import MeasurementStation, MeasurementStationCreate
from trixelmanagementclient.models import Ping as TMSPing
from trixelmanagementclient.types import Response as TMSResponse

from logging_helper import get_logger
from schema import ClientConfig, MeasurementStationConfig, TMSInfo

logger = get_logger(__name__)


class MeasurementType(str, enum.Enum):
    """Available measurement types."""

    AMBIENT_TEMPERATURE = "ambient_temperature"
    RELATIVE_HUMIDITY = "relative_humidity"


class Client:
    """Simple client which manages multiple sensors, negotiates appropriate trixels and publishes updates to a TMS."""

    # lookup server client reference
    _tsl_client: TLSClient

    # trixel_id -> (tms_id, TMSClient)
    _tms_lookup: dict[int, TMSInfo] = dict()

    # Lookup table which yields the trixel to which different measurement types contribute
    _trixel_lookup: dict[MeasurementType, int] = dict()

    # The configuration used by this client
    _config: ClientConfig

    # Indicates if the client is in-sync with the responsible TMS
    _ready: asyncio.Event = asyncio.Event()

    # Indicates that the client has finished it's work when set
    _dead: asyncio.Event = asyncio.Event()

    # User defined method which is called to persist configuration changes
    _config_persister: Callable[[ClientConfig], None] = None

    @property
    def location(self) -> Coordinate:
        """Location property getter."""
        return self._config.location

    async def set_location(self, location: Coordinate) -> bool:
        """
        Location setter which automatically triggers a trixel ID re-negotiation.

        :param location: the new location
        :returns: True if synchroization with all TMSs was successful, False otherwise
        """
        logger.debug(f"Changing location to ({location})")
        old_location = self._config.location
        self._config.location = location
        try:
            if not self.is_dead.is_set() and self._ready.is_set():
                self._ready.clear()
                await self.tls_negotiate_trixel_ids()
                await self.update_responsible_tms()
                self.persist_config()
                self._ready.set()
            else:
                self.persist_config()
            return True
        except Exception:
            self._config.location = old_location
            return False

    @property
    def k(self) -> PositiveInt:
        """K anonymity requirement property getter."""
        return self._config.k

    async def set_k(self, k: PositiveInt) -> bool:
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
                await self.sync_all_tms()
                self.persist_config()
                self._ready.set()
            else:
                self.persist_config()
        except Exception:
            self._config.k = old_k
            return False

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

    def __init__(self, config: ClientConfig, config_persister: Callable[[ClientConfig], None]):
        """Initialize the client with the given config."""
        self._config = config
        self._config_persister = config_persister

        tls_api_version = importlib.metadata.version("trixellookupclient")
        tls_major_version = packaging.version.Version(tls_api_version).major
        self._tsl_client = TLSClient(
            base_url=f"http{'s' if self._config.tls_use_ssl else ''}://{config.tls_host}/v{tls_major_version}",
        )

    def persist_config(self):
        """Call the user defined configuration persist method."""
        logger.debug("Persisting client configuration.")
        return self._config_persister(self._config)

    async def run(self):
        """Start the client, registers or resumes work at the responsible TMS."""
        self._dead.clear()
        # TODO: some notion of sensors

        # TODO: should sensors be able to be added aafterwards?
        await self.tls_negotiate_trixel_ids()
        await self.update_responsible_tms()

        await self.sync_all_tms()

        # TODO: register sensors at TMS
        # TODO: sync sensor config

        self._ready.set()

    async def tls_negotiate_trixel_ids(self):
        """Negotiate the smallest trixels for each measurement type which satisfies the k requirement."""
        # TODO: infer types from registered sensors
        types = [MeasurementType.AMBIENT_TEMPERATURE, MeasurementType.RELATIVE_HUMIDITY]

        sc = pynyhtm.SphericalCoordinate(self._config.location.latitude, self._config.location.longitude)

        trixels: dict[MeasurementType, int] = dict()

        for type_ in types:
            trixels[type_] = sc.get_htm_id(level=0)

        for level in range(0, 20):
            trixel_id = sc.get_htm_id(level=level)

            trixel_info: TLSResponse[TrixelMap] = await get_sensor_count.asyncio_detailed(
                client=self._tsl_client, trixel_id=trixel_id, types=types
            )

            if trixel_info.status_code != HTTPStatus.OK:
                logger.critical(f"Failed to negotiate trixel IDs. - {trixel_info.content}")
                raise RuntimeError(f"Failed to negotiate trixel IDs. - {trixel_info.content}")

            trixel_info: TrixelMap = trixel_info.parsed

            empty = True
            for type_ in trixel_info.sensor_counts.to_dict():
                if trixel_info.sensor_counts[type_] >= self._config.k:
                    empty = False
                    trixels[type_] = trixel_id  # TODO: consider level +1???

            if empty:
                break

        for type_, trixel_id in trixels.items():
            logger.info(
                f"Retrieved trixel (id: {trixel_id} level: {HTM.get_level(trixel_id)}) for measurement type {type_}"
            )

        self._trixel_lookup = trixels

    async def update_responsible_tms(self):
        """Retrieve the responsible TMSs for all required trixels."""
        tms_api_version = importlib.metadata.version("trixelmanagementclient")
        tms_ssl = "s" if self._config.tms_use_ssl else ""
        tms_major_version = packaging.version.Version(tms_api_version).major

        # Retrieve TMS for each trixel to which this client contributes
        for trixel_id in self._trixel_lookup.values():
            tms_response: TLSResponse[TrixelManagementServer] = await get_tms_from_trixel.asyncio_detailed(
                client=self._tsl_client, trixel_id=trixel_id
            )

            if tms_response.status_code != HTTPStatus.OK:
                logger.critical(f"Failed to retrieve TMS responsible for trixel {trixel_id})")
                raise RuntimeError(
                    f"Failed to retrieve TMS responsible for trixel {trixel_id}): - {tms_response.content}"
                )

            tms = tms_response.parsed

            if tms is None:
                raise RuntimeError(f"No TMS available for trixel {trixel_id}!")

            tms_ids = set([x.id for x in self._tms_lookup.values()])
            if len(tms_ids) > 0 and tms.id not in tms_ids:
                raise NotImplementedError("Only single TMS supported!")

            if self._config.tms_address_override is not None:
                client = TMSClient(base_url=f"http{tms_ssl}://{self._config.tms_address_override}/v{tms_major_version}")
            else:
                client = TMSClient(base_url=f"http{tms_ssl}://{tms.host}/v{tms_major_version}")
            self._tms_lookup[trixel_id] = TMSInfo(id=tms.id, client=client, host=tms.host)

        # Validate retrieved TMSs are available
        checked_tms_ids = set()
        for trixel_id, tms_response in self._tms_lookup.items():

            if tms_response.id in checked_tms_ids:
                continue
            checked_tms_ids.add(tms_response.id)

            tms_ping: TMSResponse[TMSPing] = await tms_get_ping.asyncio_detailed(client=client)
            if tms_ping.status_code != HTTPStatus.OK:
                logger.critical(f"Failed to ping TMS(id:{tms_response.id} host: {tms_response.host})")
                raise RuntimeError(
                    f"Failed to ping TMS(id:{tms_response.id} host: {tms_response.host}): {tms_ping.content}"
                )

            logger.info(f"Retrieved valid TMS(id:{tms_response.id} host: {tms_response.host}).")

    async def register_at_tms(self, tms: TMSInfo) -> MeasurementStationConfig:
        """
        Register this client at the TMS.

        :param tms: TMS at which this client should register
        :returns: Measurement station details containing the uuid and the authentication token
        """
        register_response: TMSResponse[MeasurementStationCreate] = await tms_register_station.asyncio_detailed(
            client=tms.client, k_requirement=self._config.k
        )

        if register_response.status_code != HTTPStatus.CREATED:
            logger.critical(f"Failed to register at TMS. - {register_response.content}")
            raise RuntimeError(f"Failed to register at TMS. - {register_response.content}")

        register_response: MeasurementStationCreate = register_response.parsed

        if register_response.k_requirement != self._config.k:
            logger.critical("TMS not using desired k-requirement.")
            raise RuntimeError("TMS not using desired k-requirement.")

        return MeasurementStationConfig(uuid=register_response.uuid, token=register_response.token)

    async def delete(self):
        """Remove this measurement station from all TMS where it's registered."""
        # Assumption: only a single TMS is used - all trixels share the same TMS
        tms = next(iter(self._tms_lookup.values()))

        delete_response: TMSResponse = await tms_delete_station.asyncio_detailed(
            client=tms.client, token=self._config.ms_config.token
        )

        if delete_response.status_code != HTTPStatus.NO_CONTENT:
            logger.critical(f"Failed to delete measurement station at TMS: {tms.id}")
            raise RuntimeError(f"Failed to delete measurement station at TMS: {tms.id}")

        self._config.ms_config = None
        self.persist_config()
        logger.info(f"Removed measurement station from TMS {tms.id}.")
        self._dead.set()

    async def sync_all_tms(self):
        """Synchronize this client with all TMSs."""
        # Assumption: only a single TMS is used - all trixels share the same TMS
        tms_info = next(iter(self._tms_lookup.values()))
        await self.sync_with_tms(tms=tms_info)

    async def sync_with_tms(self, tms: TMSInfo):
        """Synchronize this client with the desired TMS."""
        ms_config = self._config.ms_config

        if ms_config is None:
            self._config.ms_config = await self.register_at_tms(tms)
            self.persist_config()

        detail_response: TMSResponse[MeasurementStation] = await tms_get_station_detail.asyncio_detailed(
            client=tms.client,
            token=self._config.ms_config.token,
        )

        if detail_response.status_code != HTTPStatus.OK:
            logger.critical(f"Failed to fetch details from TMS: {tms.id}")
            raise RuntimeError(f"Failed to fetch details from TMS: {tms.id}")

        detail_response: MeasurementStation = detail_response.parsed

        if detail_response.k_requirement != self._config.k:
            update_response: TMSResponse[MeasurementStation] = await tms_update_station.asyncio_detailed(
                client=tms.client, token=self._config.ms_config.token, k_requirement=self._config.k
            )

            if update_response.status_code != HTTPStatus.OK or update_response.parsed.k_requirement != self._config.k:
                logger.critical(f"Failed to synchronize settings with TMS: {tms.id}")
                raise RuntimeError(f"Failed to synchronize settings with TMS: {tms.id}")

        # TODO: synchronize sensors

        logger.info(f"Synchronized with TMS {tms.id}")
