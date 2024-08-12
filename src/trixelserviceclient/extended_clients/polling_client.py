"""Partially managed client which periodically polls and publishes sensor updates."""

import asyncio
from asyncio import InvalidStateError
from datetime import datetime, timedelta
from typing import Callable

import httpx
from pydantic import NonNegativeInt, PositiveInt

from .. import Client
from ..exception import ServerError
from ..logging_helper import get_logger

logger = get_logger(__name__)


class PollingClient(Client):
    """Partially managed client which periodically polls and publishes sensor updates."""

    async def run(
        self,
        get_updates: Callable[[], dict[int, tuple[datetime, float]]],
        retry_interval: timedelta = timedelta(seconds=30),
        max_retries: PositiveInt | None = 10,
        polling_interval: timedelta = timedelta(seconds=60),
        delete: bool = False,
    ):
        """
        Run the client by initializing it using `start` and providing sensor updates in fixed intervals.

        If the network does not respond properly after `max_retries` times while starting the client, an exception is
        raised. A restart of the client is attempted if it finds itself in a 'not-ready' situation for more than
        `retry_interval` a re-initialization is attempted. A brief `not-ready` state must be tolerated, in case some
        client settings are changed and synchronization is required.


        :param get_updates: method which gets updated values for all sensors
        :param retry_interval: wait period in between initialization attempts (in seconds), timeout time for `ready`
        state
        :param max_retries: maximum number of retries before aborting, endless retries if None
        :param polling_interval: time period which determines how often sensor values are published
        :param delete: if set to true, the sensor will be delete from the TMS once it's ready
        """
        retries: NonNegativeInt = 1
        last_ready: datetime = datetime.now() - retry_interval * 2
        last_update: datetime = datetime.now() - polling_interval * 2
        while not self.is_dead.is_set():
            if not self.is_ready.is_set() and datetime.now() - last_ready > retry_interval:
                # Attempt to start client and re-initialize if "ready-state" times out
                try:
                    await self.start()
                    retries = 1
                except (ServerError, InvalidStateError, httpx.HTTPError) as e:
                    if max_retries is None or retries <= max_retries:
                        logger.warning(f"Failed to start client, retrying ({retries}/{max_retries}): {e}")
                        retries += 1
                        last_ready = datetime.now()
                        continue
                    else:
                        logger.critical("Maximum retries exceeded, client did not start successfully!")
                        raise
                except Exception:
                    raise
            elif self.is_ready.is_set():
                # Periodically publish sensor updates
                last_ready = datetime.now()
                if last_ready - last_update > polling_interval:
                    last_update = datetime.now()
                    try:
                        if not delete:
                            await self.publish_values(updates=get_updates())
                        else:
                            await self.delete()
                            return
                    except (ServerError, InvalidStateError, httpx.HTTPError) as e:
                        logger.warning(f"Failed to publish values: {e}")
                    except Exception:
                        raise

            # Spinlock until client is not ready or dead
            await asyncio.sleep(0.1)
