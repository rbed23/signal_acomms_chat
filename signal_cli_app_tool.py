import json
import logging
import sys

import requests
import signal
import subprocess
import websocket
import threading
import time

ROSBRIDGE_PORT = 9090
ROSBRIDGE_URL = f"ws://localhost:{ROSBRIDGE_PORT}"
SIGNAL_CLI_API_PORT = 8080
SIGNAL_CLI_API_URL = f"http://localhost:{SIGNAL_CLI_API_PORT}/v1"
SIGNAL_NUMBER = "+16197191785"  # CORDC-ROS-registered device

LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

sh = logging.StreamHandler(stream=sys.stdout)
sh.setLevel(logging.DEBUG)
sh.setFormatter(formatter)
LOGGER.addHandler(sh)

fh = logging.FileHandler('logs/signal_cli_app.log')
fh.setLevel(logging.DEBUG)
fh.setFormatter(formatter)
LOGGER.addHandler(fh)

EVENT_FLAG = threading.Event()

# ------ ROSBRIDGE WEBSOCKET ------
def rosbridge_thread():
    ws = websocket.WebSocket(
        environ={
            "wsgi.websocket_port": ROSBRIDGE_PORT
        },
        socket=None,
        rfile=None
    )
    ws.connect(ROSBRIDGE_URL)

    # subscribe to outgoing topic
    ws.send(json.dumps({
        "op": "subscribe",
        "topic": "/chat_output",
        "type": "std_msgs/String"
    }))

    while True:
        message = json.loads(ws.recv())
        if message.get("op") == "publish":
            content = message["msg"]["data"]
            subprocess.run([
                "signal-cli",
                "-u", SIGNAL_NUMBER,
                "send", "<recipient-number>",
                "-m", content
            ])



# ------ SIGNAL CLI LISTENER ------
def signal_listener():
    # proc = subprocess.Popen(
    #     ["signal-cli", "-u", SIGNAL_NUMBER, "receive", "-t"],
    #     stdout=subprocess.PIPE,
    #     text=True
    # )

    headers = {
        "Content-Type": "application/json",
    }

    r_url = f"{SIGNAL_CLI_API_URL}/receive/{SIGNAL_NUMBER}"
    r = requests.get(r_url, headers=headers)
    LOGGER.info(f'Listening: {r.url}')
    rjson = r.json()
    LOGGER.info(json.dumps(rjson, indent=2)) if rjson else None

    for each in rjson:
        if "envelope" in each:
            envelope = each["envelope"]
            sender = envelope["sourceName"]
            ts_received = envelope["timestamp"]
            if 'dataMessage' in envelope:
                data = envelope["dataMessage"]["message"]
                data_to_print = f"{sender}: {ts_received}: {data}"
                LOGGER.info(f'LOGGING: {data_to_print}')
                ws = websocket.WebSocket()
                ws.connect(ROSBRIDGE_URL)
                ws.send(json.dumps({
                    "op": "publish",
                    "topic": "/chat_input",
                    "msg": {"data": data}
                }))
                ws.close()
            elif 'typingMessage' in envelope:
                typing_message = envelope["typingMessage"]["action"]
                typing_msg_to_print = f'{sender}: {ts_received}: {typing_message}'
                LOGGER.info(f'LOGGING: {typing_msg_to_print}')


    EVENT_FLAG.clear()
    return


if __name__ == "__main__":
    threading.Thread(target=rosbridge_thread).start()

    LOGGER.info("Kicking off...")
    while True:
        if not EVENT_FLAG.is_set():
            EVENT_FLAG.set()
            threading.Thread(target=signal_listener).start()
        else:
            time.sleep(5)
    print("Exited.")