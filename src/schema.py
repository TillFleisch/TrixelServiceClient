"""Global schemas related to the trixel service client."""

import enum

from pydantic import UUID4, BaseModel, ConfigDict, PositiveFloat, PositiveInt
from pydantic_extra_types.coordinate import Coordinate
from trixelmanagementclient import Client as TMSClient


class MeasurementType(str, enum.Enum):
    """Available measurement types."""

    AMBIENT_TEMPERATURE = "ambient_temperature"
    RELATIVE_HUMIDITY = "relative_humidity"


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

    # The precise geographic location of the measurement station
    location: Coordinate

    # The anonymity requirement, which should be used when hiding the location via Trixels
    k: PositiveInt

    tls_host: str
    tls_use_ssl: bool = True
    tms_use_ssl: bool = True
    tms_address_override: str | None = None
    ms_config: MeasurementStationConfig | None = None
    sensors: list[Sensor] = list()
