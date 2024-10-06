# Trixel Service Client

The *Trixel Service Client (TSC)* is a single unified python module which is used to participate in privacy aware environmental monitoring.
This module is responsible for interacting with the *Trixel Lookup Service (TLS)* and the *Trixel Management Service (TMS)* to provide updates to the trixel-based sensor network.

This module currently only support contributing clients.

## Usage

The core `Client` class contains large parts of the logic and allows for the implementations of derivatives which persist the clients configuration differently.
Persisting the configuration is required as it contains the measurement stations unique identifier and authentication token.

Examples for client implementations and extensions therefore can be found under [src/trixelserviceclient/extended_clients/](src/trixelserviceclient/extended_clients/).
The [PollingPickleClient](src/trixelserviceclient/extended_clients/pickle_client.py) is a complete client implementation that can persist the client configuration in a pickle file for future use.
It requires the definition of a `get_updates` method which provides the to-be-submitted values for the registered sensors.
This variant of the implementation performs all necessary steps for contribution by itself, which includes registration, configuration change synchronization and providing measurement updates.

The `Client` class also build the foundation for the [Trixel service bridges](https://github.com/TillFleisch/TrixelServiceBridges/) and the [Home Assistant Trixel contribution Integration](https://github.com/TillFleisch/TrixelNetworkIntegration).

## Development

The client implementation uses both the generated [TSM python client module](https://pypi.org/project/trixelmanagementclient/) and the [TLS python client module](https://pypi.org/project/trixellookupclient/).
They are listed in the requirements file among other dependencies.
[Pre-commit](https://pre-commit.com/) is used to enforce code-formatting, formatting tools are mentioned [here](.pre-commit-config.yaml).

The resulting python module can be built with pythons `build` module, which is also performed by the CI/CD pipeline.
