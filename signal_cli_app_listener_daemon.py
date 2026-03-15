"""
Listener for Signal CLI App
Injects message from Signal App to inject into the ROSBRIDGE Server network
"""
from datetime import datetime, timezone
import json
from logging import FileHandler, DEBUG, INFO, WARNING, ERROR
import threading
import time

import requests
import websocket

from utils.configs_logger import LOGGER, FORMATTER

from utils.configs_signal_cli import (
    SIGNAL_CLI_API_RECEIVE_V1,
    SIGNAL_CLI_API_PORT,
    SIGNAL_CLI_API_SERVER_NUMBER,
    SIGNAL_CLI_API_MESSAGE_WAIT_SECONDS
)

from utils.configs_rosbridge_server import (
    ROSBRIDGE_WS_URL,
    CHAT_DOWNLINK_TOPIC
)

fh = FileHandler('logs/signal_cli_app_listener.log')
fh.setLevel(DEBUG)
fh.setFormatter(FORMATTER)
LOGGER.addHandler(fh)


_LOOP_RETRIES_TO_LOG = 5

# ------ SIGNAL CLI LISTENER ------
def signal_listener():
    r_url = SIGNAL_CLI_API_RECEIVE_V1.substitute(
        {
            "PORT": SIGNAL_CLI_API_PORT,
            "NUMBER": SIGNAL_CLI_API_SERVER_NUMBER
        }
    )
    LOGGER.debug(f'V1 SIGNAL CLI TX URL SUBSTITUTION: {r_url}')

    LOGGER.info(f"Listener will check for new "
                f"SIGNAL CLI API RECEIVED messages every "
                f"[{SIGNAL_CLI_API_MESSAGE_WAIT_SECONDS}] seconds")
    LOGGER.info(f'Listening: {r_url}')

    _attempts = 0
    while True:
        # check for new Signal messages every period
        time.sleep(SIGNAL_CLI_API_MESSAGE_WAIT_SECONDS)
        _attempts += 1
        if _attempts > _LOOP_RETRIES_TO_LOG:
            LOGGER.debug(f"loop-check still active... "
                         f"(repeats every "
                         f"{SIGNAL_CLI_API_MESSAGE_WAIT_SECONDS * _LOOP_RETRIES_TO_LOG} "
                         f"seconds)")
            _attempts = 0
        try:
            r = requests.get(
                r_url,
                headers={"Content-Type": "application/json"}
            )
        except requests.exceptions.ConnectionError as e:
            LOGGER.warning(f"Connection Error to {r_url} ({e})")
            _attempts += 1
            continue
        except requests.exceptions.Timeout as e:
            LOGGER.warning(f"Timeout to {r_url} ({e})")
            _attempts += 1
            continue
        except requests.exceptions.RequestException as e:
            LOGGER.warning(f"Request to {r_url} Failed ({e})")
            _attempts += 1
            continue

        try:
            rjson = r.json()
        except json.decoder.JSONDecodeError as e:
            LOGGER.warning(f'V1 SIGNAL CLI RECEIVE ERROR ({e}): {r.text}')
            _attempts += 1
            continue
        else:
            if not rjson:
                _attempts += 1
                # nothing received,
                # nothing to do
                continue
            elif 'error' in rjson:
                _attempts += 1
                LOGGER.warning(f"Response Error: {rjson.get('error').strip()}; "
                               f"check the internet connection.")
                continue

        LOGGER.debug(f"SIGNAL CLI RESPONSE JSON:\n"
                     f"{json.dumps(rjson, indent=2)}") if rjson else None

        for envelope in rjson:
            if 'envelope' in envelope:
                env = envelope.get("envelope")
                sender = env.get("source")
                sender_name = env.get("sourceName")
                sender_number = env.get("sourceNumber")
                ts_received = env.get("timestamp")
                if 'dataMessage' in env:
                    data = env.get("dataMessage")["message"]
                    ws = websocket.WebSocket()
                    ws.connect(ROSBRIDGE_WS_URL)
                    ws.send(json.dumps({
                        "op": "publish",
                        "topic": CHAT_DOWNLINK_TOPIC,
                        "msg": {
                            "data": data,
                            "source": sender,
                            "source_name": sender_name
                        }
                    }))
                    ws.close()
                    ts = datetime.fromtimestamp(int(ts_received)/1000, tz=timezone.utc)
                    LOGGER.info(f"LOGGING: {sender, sender_name} sent '{data}' at {ts}")
                elif 'receiptMessage' in env:
                    # TODO: handle receiptMessage envelope Type
                    receipt_message = env.get("receiptMessage")
                    status = ''
                    if receipt_message['isDelivery']:
                        status = 'Delivered'
                    elif receipt_message['isViewed']:
                        status = 'Viewed'
                    elif receipt_message['isRead']:
                        status = 'Read'
                    else:
                        status = 'Unknown'
                    ts = datetime.fromtimestamp(int(receipt_message['when'])/1000, tz=timezone.utc)
                    LOGGER.info(f"LOGGING: Message Receipt Status from {sender, sender_name} '{status}' at: {ts}")
                elif 'typingMessage' in env:
                    # TODO: properly handle typingMessage envelope Type
                    typing_message = env.get("typingMessage")["action"]
                    ts = datetime.fromtimestamp(int(env.get("typingMessage")['timestamp'])/1000, tz=timezone.utc)
                    LOGGER.info(f'LOGGING: {sender, sender_name} {typing_message} Typing at {ts}')


if __name__ == "__main__":
    t = None
    try:
        LOGGER.info("SIGNAL CLI LISTENER Kicking off...")
        t = threading.Thread(target=signal_listener)
        t.start()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        LOGGER.error(f'Caught an unexpected error: {e}')

    t.join() if t is not None and t is threading.Thread and t.is_alive() else None
    LOGGER.info("SIGNAL CLI LISTENER Exited.\n"
                "---------------------------")
