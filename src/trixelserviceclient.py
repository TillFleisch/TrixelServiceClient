"""Simple client for the trixel based environmental observation sensor network."""

import enum
import importlib
from http import HTTPStatus

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
from trixelmanagementclient.models import Ping as TMSPing
from trixelmanagementclient.types import Response as TMSResponse

from logging_helper import get_logger
from schema import ClientConfig

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
    _tms_lookup: dict[int, tuple[int, TMSClient]] = dict()

    # Lookup table which yields the trixel to which different measurement types contribute
    _trixel_lookup: dict[MeasurementType, int] = dict()

    # The configuration used by this client
    _config: ClientConfig

    # Indicates if the client is in-sync with the responsible TMS
    _ready: bool = False

    @property
    def location(self) -> Coordinate:
        """Location property getter."""
        return self._config.location

    @location.setter
    def location(self, location: Coordinate):
        """Location setter which automatically triggers a trixel ID re-negotiation."""
        self._config.location = location
        # TODO: perform re-negotiation

    @property
    def k(self) -> PositiveInt:
        """K anonymity requirement property getter."""
        return self._config.k

    @k.setter
    def k(self, k: PositiveInt):
        """K anonymity requirement setter which synchronies with the TMS."""
        # TODO: update k at TMS (then proceed with local change)
        self._config.k = k

    def __init__(self, config: ClientConfig):
        """Initialize the client with the given config."""
        self._config = config

        tls_api_version = importlib.metadata.version("trixellookupclient")
        tls_major_version = packaging.version.Version(tls_api_version).major
        self._tsl_client = TLSClient(
            base_url=f"http{'s' if self._config.tls_use_ssl else ''}://{config.tls_host}/v{tls_major_version}",
        )

    async def run(self):
        """Start the client, registers or resumes work at the responsible TMS."""
        # TODO: some notion of sensors

        # TODO: should sensors be able to be added aafterwards?

        await self.tls_negotiate_trixel_ids()
        await self.update_responsible_tms()

        # TODO: register at tms
        # TODO: register sensors at TMS
        # TODO: sync sensor config

        self._ready = True

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
            logger.debug(
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
            tms_info: TLSResponse[TrixelManagementServer] = await get_tms_from_trixel.asyncio_detailed(
                client=self._tsl_client, trixel_id=trixel_id
            )

            if tms_info.status_code != HTTPStatus.OK:
                logger.critical(f"Failed to retrieve TMS responsible for trixel {trixel_id})")
                raise RuntimeError(f"Failed to retrieve TMS responsible for trixel {trixel_id}): - {tms_info.content}")

            tms = tms_info.parsed

            if tms is None:
                raise RuntimeError(f"No TMS available for trixel {trixel_id}!")

            tms_ids = set([x[0] for x in self._tms_lookup.values()])
            if len(tms_ids) > 0 and tms.id not in tms_ids:
                raise NotImplementedError("Only single TMS supported!")

            if self._config.tms_address_override is not None:
                client = TMSClient(base_url=f"http{tms_ssl}://{self._config.tms_address_override}/v{tms_major_version}")
            else:
                client = TMSClient(base_url=f"http{tms_ssl}://{tms.host}/v{tms_major_version}")
            self._tms_lookup[trixel_id] = (tms.id, client)

        # Validate retrieved TMSs are available
        checked_tms_ids = set()
        for trixel_id, tms_tuple in self._tms_lookup.items():

            tms_id, tms_client = tms_tuple
            if tms_id in checked_tms_ids:
                continue
            checked_tms_ids.add(tms_id)

            tms_ping: TMSResponse[TMSPing] = await tms_get_ping.asyncio_detailed(client=client)
            if tms_ping.status_code != HTTPStatus.OK:
                logger.critical(f"Failed to ping TMS(id:{tms_id} host: {tms_client._base_url})")
                raise RuntimeError(f"Failed to ping TMS(id:{tms_id} host: {tms_client._base_url}): {tms_ping.content}")

            logger.debug(f"Retrieved valid TMS(id:{tms_id} host: {tms_client._base_url}).")
