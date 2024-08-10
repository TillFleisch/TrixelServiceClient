"""Client implementations which persist configuration changes using pickle."""

import pickle

from pydantic import FilePath

from .. import Client
from ..schema import ClientConfig
from .polling_client import PollingClient


class PickleClient(Client):
    """A pickle based client implementation which pickles the client configuration to persist it."""

    pickle_path: FilePath

    def __init__(self, file_path: FilePath, config: ClientConfig | None = None, override_config: bool = False):
        """
        Load the configuration from a pickle file or use the provided config.

        :param file_path: Path to a pickle file which is used/created
        :param config: client configuration which is used in case no pickle file is found or when override is enabled
        :param override_config: forces the use of the provided config file instead of an existing pickle
        """
        self.pickle_path = file_path
        if not override_config:
            try:
                file = open(self.pickle_path, "rb")
                config = pickle.load(file)
            except FileNotFoundError:
                if config is None:
                    raise

        super().__init__(config, None)

        if override_config:
            self._persist_config()

    def _persist_config(self):
        """Persist the clients configuration using pickle at the desired path."""
        file = open(self.pickle_path, "wb")
        pickle.dump(self._config, file)
        file.close()


class PollingPickleClient(PollingClient, PickleClient):
    """A polling client which persists the client configuration using pickle."""

    pass
