"""Global schemas related to the trixel service client."""

import enum

import httpx
from pydantic import UUID4, BaseModel, ConfigDict, Field, PositiveFloat, PositiveInt
from pydantic_extra_types.coordinate import Coordinate
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


class TMSInfo(BaseModel):
    """Schema which hold details related to a TMS."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    id: int
    host: str
    client: TMSClient


class Sensor(BaseModel):
    """Schema for describing sensors including details."""

    model_config = ConfigDict(from_attributes=True)

    measurement_type: MeasurementType
    accuracy: PositiveFloat | None = None
    sensor_name: str | None = None
    sensor_id: int | None = None


class MeasurementStationConfig(BaseModel):
    """Measurement station details which are used for authentication at the TMS."""

    uuid: UUID4
    token: str


class ClientConfig(BaseModel):
    """Configuration schema which defines the behavior of the client."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # The precise geographic location of the measurement station
    location: Coordinate

    # The anonymity requirement, which should be used when hiding the location via Trixels
    k: PositiveInt

    # The maximum trixel depth to which the client descends
    max_depth: PositiveInt = Field(24, ge=1, le=24)

    client_timeout: httpx.Timeout = httpx.Timeout(30.0)
    tls_host: str
    tls_use_ssl: bool = True
    tms_use_ssl: bool = True
    tms_address_override: str | None = None
    ms_config: MeasurementStationConfig | None = None
    sensors: list[Sensor] = list()
