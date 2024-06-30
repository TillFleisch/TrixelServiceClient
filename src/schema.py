"""Global schemas related to the trixel service client."""

from pydantic import BaseModel, PositiveInt
from pydantic_extra_types.coordinate import Coordinate


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
