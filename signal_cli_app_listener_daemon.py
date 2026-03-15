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


class EnvelopeMetaData:
    def __init__(self, envelope):
        if 'envelope' in envelope:
            envelope = envelope.get("envelope")
        _source = envelope.get('source')
        self._source_name = envelope.get('sourceName')
        self._source_number = envelope.get('sourceNumber')
        self.source: str = self._source_name if self._source_name is not None \
            else self._source_number if self._source_number is not None \
            else _source
        _source_id = envelope.get('sourceUuid')
        _source_device = envelope.get('sourceDevice')

        self.server_ts_event: float = int(envelope.get('timestamp'))/1000
        self.server_ts_received: float = int(envelope.get('serverReceivedTimestamp'))/1000
        self.server_ts_delivered: float = int(envelope.get('serverDeliveredTimestamp'))/1000

    def __str__(self):
        return f"EnvelopeMetaData: {vars(self)}"

class DataMessage:
    def __init__(self, data):
        _data_message_pkg = self.verify(data)
        self._envelope = EnvelopeMetaData(data)
        self.source = self._envelope.source
        self.message = _data_message_pkg['message']
        self.message_generated_ts = int(_data_message_pkg['timestamp'])/1000
        #self.message_expires_in = _data_message_pkg['expiresInSeconds']
        #self.message_expiration_update = _data_message_pkg['isExpirationUpdate']
        #self.message_view_once = _data_message_pkg['viewOnce']
        self._from_account = _data_message_pkg.get('account')
        self.packaged_message = f"{self.source.upper()}]{self.message}"

    @staticmethod
    def verify(envelope):
        if 'dataMessage' not in envelope.keys():
            raise AttributeError('dataMessage missing')
        return envelope['dataMessage']


class TypingMessage:
    def __init__(self, data):
        _typing_message_pkg = self.verify(data)
        self._envelope = EnvelopeMetaData(data)
        self.source = self._envelope.source
        self.action = _typing_message_pkg['action']
        self.action_generated_ts = int(_typing_message_pkg['timestamp'])/1000
        #self.message_expires_in = _typing_message_pkg['expiresInSeconds']
        #self.message_expiration_update = _typing_message_pkg['isExpirationUpdate']
        #self.message_view_once = _typing_message_pkg['viewOnce']
        self._from_account = _typing_message_pkg.get('account')
        self.packaged_action = f"{self.source.upper()}]{self.action}"

    @staticmethod
    def verify(envelope):
        if 'typingMessage' not in envelope.keys():
            raise AttributeError('typingMessage missing')
        return envelope['typingMessage']


class ReceiptMessage:
    def __init__(self, data):
        _receipt_message_pkg = self.verify(data)
        self._envelope = EnvelopeMetaData(data)
        self.source = self._envelope.source
        self.status, self.status_ts = self.get_status(_receipt_message_pkg)
        self.receipt_generated_ts = _receipt_message_pkg['timestamps']
        self._from_account = _receipt_message_pkg.get('account')
        self.packaged_action = f"{self.source.upper()}]{self.status}"

    @staticmethod
    def get_status(status_data: dict):
        status = 'Unknown'
        if status_data.get('isDelivery'):
            status = 'Delivered'
        elif status_data.get('isRead'):
            status = 'Read'
        elif status_data.get('isViewed'):
            status = 'Viewed'
        return status, int(status_data.get('when'))/1000

    @staticmethod
    def verify(envelope: dict):
        if 'receiptMessage' not in envelope.keys():
            raise AttributeError('receiptMessage missing')
        return envelope['receiptMessage']


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
                    dm = DataMessage(env)
                    dm.message = env.get("dataMessage")["message"]
                    ws = websocket.WebSocket()
                    ws.connect(ROSBRIDGE_WS_URL)
                    ws.send(json.dumps({
                        "op": "publish",
                        "topic": CHAT_DOWNLINK_TOPIC,
                        "msg": {
                            "data": dm.message,
                            "source": dm._envelope._source_number,
                            "source_name": dm._envelope._source_name
                        }
                    }))
                    ws.close()
                    ts = datetime.fromtimestamp(dm.message_generated_ts, tz=timezone.utc)
                    LOGGER.info(f"LOGGING: {sender, sender_name} sent '{dm.packaged_message}' at {ts}")
                elif 'receiptMessage' in env:
                    # TODO: handle receiptMessage envelope Type
                    rm = ReceiptMessage(env)
                    ts = datetime.fromtimestamp(rm.status_ts, tz=timezone.utc)
                    LOGGER.info(f"LOGGING: Message Receipt Status from {sender, sender_name} '{rm.status}' at: {ts}")
                elif 'typingMessage' in env:
                    # TODO: properly handle typingMessage envelope Type
                    tm = TypingMessage(env)
                    ts = datetime.fromtimestamp(tm.action_generated_ts, tz=timezone.utc)
                    LOGGER.info(f'LOGGING: {sender, sender_name} {tm.action} Typing at {ts}')


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
