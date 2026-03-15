import json
from logging import FileHandler, DEBUG, INFO, WARNING, ERROR
import threading

import requests
import websocket

from utils.configs_logger import LOGGER, FORMATTER

from utils.configs_signal_cli import (
    SIGNAL_CLI_API_PORT,
    SIGNAL_CLI_API_SERVER_NUMBER,
    SIGNAL_CLI_API_SEND_V2,

)

from utils.configs_rosbridge_server import (
    ROSBRIDGE_WS_URL,
    CHAT_UPLINK_TOPIC,
    CHAT_UPLINK_TYPE
)

fh = FileHandler('logs/signal_cli_app_rosbridge.log')
fh.setLevel(DEBUG)
fh.setFormatter(FORMATTER)
LOGGER.addHandler(fh)


# ------ ROSBRIDGE WEBSOCKET ------
def rosbridge_thread():
    ws = websocket.WebSocket()
    try:
        ws.connect(ROSBRIDGE_WS_URL)
    except Exception as wse:
        LOGGER.error(f"Caught Error Connecting to WS: {(wse,)}")

    # subscribe to outgoing/uplink topic
    # registration will connect messages from acoustic network
    # out/up to SIGNAL App via SIGNAL CLI API
    ws.send(json.dumps({
        "op": "subscribe",
        "topic": CHAT_UPLINK_TOPIC,
        "type": CHAT_UPLINK_TYPE
    }))
    try:
        while True:
            ws_msg = ws.recv()
            LOGGER.debug(f"WEBSOCKET RX: {ws_msg}")
            try:
                message = json.loads(ws_msg)
            except json.decoder.JSONDecodeError:
                continue
            LOGGER.info(f"Received message: {ws_msg}")

            if message.get("op") == "publish" and message.get("topic") == CHAT_UPLINK_TOPIC:
                msg_data = message.get("msg")
                if msg_data is None or msg_data.get("data") is None:
                    LOGGER.warning(f"No actual message data received: {ws_msg}")
                    LOGGER.debug(f"Received JSON to DEBUG:\n{ws_msg}")
                    continue
                LOGGER.debug(f"WEBSOCKET MESSAGE: {message}")
                src_info = message.get("msg").get("source_name") if message.get("msg").get("source_name") is not None else message.get("msg").get("source")
                d = {
                    "message": f"{src_info}]{msg_data.get('data')}",
                    "number": SIGNAL_CLI_API_SERVER_NUMBER,
                    "recipients": [
                        msg_data.get("recipient-number")
                    ]
                }
                LOGGER.debug(f"WS DATA FOR SIGNAL API POST: {d}")

                p = requests.post(
                    SIGNAL_CLI_API_SEND_V2.substitute(
                        {
                            "PORT": SIGNAL_CLI_API_PORT
                        }
                    ),
                    headers={"Content-Type": "application/json"},
                    json=d
                )
                LOGGER.debug(f"SIGNAL API POST RESPONSE, URL: {p.status_code, p.url}")
                if p.ok:
                    LOGGER.info(f"WB MSG Posted to SIGNAL API Successful.")
                else:
                    LOGGER.warning(f"WS MSG POST Failed with code: {p.status_code}")
                    LOGGER.warning(f"Failure Response: {p.text}")
            else:
                LOGGER.warning(f"MSG Doesnt Meet Op ('publish') or Topic ('{CHAT_UPLINK_TOPIC}') Requirements")
    except Exception as e:
        LOGGER.error(f'***Caught an unexpected error: {(e,)}')
        ws.close()
        LOGGER.warning(f'WEBSOCKET Closed.')


if __name__ == "__main__":
    t = None
    try:
        LOGGER.info("ROSBRIDGE HANDLER Kicking off...")
        t = threading.Thread(target=rosbridge_thread)
        t.start()
    except KeyboardInterrupt:
        print("here...")
    except Exception as e:
        LOGGER.error(f'---Caught an unexpected error: {(e,)}')

    t.join() if t is not None and t is threading.Thread and t.is_alive() else None
    LOGGER.info("ROSBRIDGE HANDLER Exited.\n"
                "---------------------------")
