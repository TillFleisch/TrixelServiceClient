"""Global schemas related to the trixel service client."""

import enum
from uuid import UUID

import httpx
from trixelmanagementclient import Client as TMSClient


class MeasurementType(str, enum.Enum):
    """Available measurement types."""

    AMBIENT_TEMPERATURE = "ambient_temperature"
    RELATIVE_HUMIDITY = "relative_humidity"


class SeeOtherReason(str, enum.Enum):
    """Enum which indicates the reason for a see other message."""

    WRONG_TMS = "wrong_tms"
    CHANGE_TRIXEL = "change_trixel"


class TrixelLevelChange(enum.StrEnum):
    """Enum which indicates the actions which should be taken by a client to maintain the k-anonymity requirement."""

    KEEP = "keep"
    INCREASE = "increase"
    DECREASE = "decrease"


class TMSInfo:
    """Schema which hold details related to a TMS."""

    id: int
    host: str
    client: TMSClient

    def __init__(self, id: int, host: str, client: TMSClient):
        """Initialize this TMSInfo object."""
        self.id = id
        self.host = host
        self.client = client


class Sensor:
    """Schema for describing sensors including details."""

    measurement_type: MeasurementType
    accuracy: float | None = None
    sensor_name: str | None = None
    sensor_id: int | None = None

    def __init__(
        self,
        measurement_type: MeasurementType,
        accuracy: float | None = None,
        sensor_name: str | None = None,
        sensor_id: int | None = None,
    ):
        """Initialize this sensor object."""
        self.measurement_type = measurement_type
        self.accuracy = accuracy
        self.sensor_name = sensor_name
        self.sensor_id = sensor_id


class MeasurementStationConfig:
    """Measurement station details which are used for authentication at the TMS."""

    uuid: UUID
    token: str

    def __init__(self, uuid: UUID, token: str):
        """Initialize this MeasurementStationConfig object."""
        self.uuid = uuid
        self.token = token


class Coordinate:
    """Simple coordinate class which holds latitude and longitude information."""

    _latitude: float = 0
    _longitude: float = 0

    def __init__(self, latitude: float, longitude: float):
        """Initialize this coordinate object."""
        self.latitude = latitude
        self.longitude = longitude

    @property
    def latitude(self) -> int:
        """Get the latitude of this coordinate object."""
        return self._latitude

    @property
    def longitude(self) -> int:
        """Get the longitude of this coordinate object."""
        return self._longitude

    @latitude.setter
    def latitude(self, value):
        """Set the latitude value of this coordinate."""
        if value > 90.0 or value < -90.0:
            raise ValueError("Latitude must be between -90 and +90")
        self._latitude = value

    @longitude.setter
    def longitude(self, value):
        """Set the longitude value of this coordinate."""
        if value > 180.0 or value < -180.0:
            raise ValueError("Longitude must be between -90 and +90")
        self._longitude = value


class ClientConfig:
    """Configuration schema which defines the behavior of the client."""

    # The precise geographic location of the measurement station
    location: Coordinate

    # The anonymity requirement, which should be used when hiding the location via Trixels
    k: int

    # The maximum trixel depth to which the client descends
    _max_depth: int = 24

    client_timeout: httpx.Timeout = httpx.Timeout(30.0)
    tls_host: str
    tls_use_ssl: bool = True
    tms_use_ssl: bool = True
    tms_address_override: str | None = None
    ms_config: MeasurementStationConfig | None = None
    sensors: list[Sensor] = list()

    def __init__(
        self,
        location: Coordinate,
        k: int,
        tls_host: str,
        max_depth: int = 24,
        client_timeout: httpx.Timeout = httpx.Timeout(30.0),
        tls_use_ssl: bool = True,
        tms_use_ssl: bool = True,
        tms_address_override: str | None = None,
        ms_config: MeasurementStationConfig | None = None,
        sensors: list[Sensor] = list(),
    ):
        """Initialize the ClientConfig object with the given user config."""
        self.location = location
        self.k = k
        self.max_depth = max_depth
        self.client_timeout = client_timeout
        self.tls_host = tls_host
        self.tls_use_ssl = tls_use_ssl
        self.tms_use_ssl = tms_use_ssl
        self.tms_address_override = tms_address_override
        self.ms_config = ms_config
        self.sensors = sensors

    @property
    def max_depth(self):
        """Get the current max depth of this client config."""
        return self._max_depth

    @max_depth.setter
    def max_depth(self, value):
        """Set the max_depth value of this coordinate."""
        if value >= 1 and value <= 24:
            self._max_depth = value
        else:
            raise ValueError("Max depth must be between 1 and 24 (inclusive)!")
