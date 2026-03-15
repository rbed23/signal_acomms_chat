import asyncio
import json
from logging import FileHandler, DEBUG, INFO, WARNING, ERROR

import websockets
from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError

from utils.configs_logger import LOGGER, FORMATTER
from utils.configs_rosbridge_server import ROSBRIDGE_PORT, CHAT_DOWNLINK_TOPIC, CHAT_UPLINK_TOPIC


fh = FileHandler('logs/signal_cli_app_ws_server.log')
fh.setLevel(DEBUG)
fh.setFormatter(FORMATTER)
LOGGER.addHandler(fh)

CONNECTIONS = set()


async def echo(websocket):
    if websocket not in CONNECTIONS:
        CONNECTIONS.add(websocket)
        LOGGER.info(f"Adding Connection: {websocket} (id: {websocket.id})")
        for k, v in vars(websocket).items():
            LOGGER.debug(f"  {k}: {v}")
        origin_connections = [x.request.headers.get("Origin") or x.request.headers.get("Host") for x in CONNECTIONS]
        LOGGER.info(f"CONNECTIONS [{len(CONNECTIONS)}]: {origin_connections}")
    try:
        async for message in websocket:
            LOGGER.info(f"Sending '{message}'to {origin_connections}")
            websockets.broadcast(CONNECTIONS, message)
            try:
                msg = json.loads(message)
                print(msg)
                if msg.get('op') == 'publish' and msg.get('topic') == CHAT_DOWNLINK_TOPIC:
                    LOGGER.info(f"FAKE ROSBRIDGE SERVER ACTION: "
                                f"'{msg.get('op').upper()} message "
                                f"\'{msg.get('msg').get('source_name').upper()}]{msg.get('msg').get('data')}\' "
                                f"on topic \'{msg.get('topic')}\' "
                                f"to ACOUSTIC NETWORK "
                                f"from Source: {msg.get('msg').get('source'), msg.get('msg').get('source_name')}")
                if msg.get('op') == 'publish' and msg.get('topic') == CHAT_UPLINK_TOPIC:
                    _ph = f"{msg.get('msg').get('source_name').upper()}]" if msg.get('msg').get('source_name') is not None else f"{msg.get('msg').get('source')}:"
                    LOGGER.info(f"FAKE ROSBRIDGE SERVER ACTION: "
                                f"'{msg.get('op').upper()} message "
                                f"\'{_ph}{msg.get('msg').get('data')}\' "
                                f"on topic \'{msg.get('topic')}\' "
                                f"to SIGNAL APP "
                                f"from Source: {msg.get('msg').get('source'), msg.get('msg').get('source_name')}")
            except AttributeError:
                pass
    except (ConnectionClosedOK, ConnectionClosedError) as e:
        LOGGER.warning(f"Connection closed: {e}")
    except Exception as e:
        LOGGER.error(f"An unexpected error occurred: {(e,)}")


async def main():
    async with websockets.serve(echo, "localhost", ROSBRIDGE_PORT):
        LOGGER.info("Server started.")
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    try:
        LOGGER.info("Starting WS server...")
        asyncio.run(main())
    except KeyboardInterrupt:
        LOGGER.info("Shutting down...")
