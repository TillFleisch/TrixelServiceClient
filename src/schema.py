"""Global schemas related to the trixel service client."""

from pydantic import UUID4, BaseModel, ConfigDict, PositiveInt
from pydantic_extra_types.coordinate import Coordinate
from trixelmanagementclient import Client as TMSClient


class TMSInfo(BaseModel):
    """Schema which hold details related to a TMS."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    id: int
    host: str
    client: TMSClient


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
