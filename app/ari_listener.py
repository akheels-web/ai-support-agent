import asyncio
import json
import requests
import websockets

from app.config import (
    ASTERISK_ARI_URL,
    ASTERISK_ARI_USER,
    ASTERISK_ARI_PASSWORD,
    ASTERISK_ARI_APP,
)

ARI_WS_URL = (
    ASTERISK_ARI_URL.replace("http", "ws")
    + f"/ari/events?api_key={ASTERISK_ARI_USER}:{ASTERISK_ARI_PASSWORD}"
    + f"&app={ASTERISK_ARI_APP}"
)


def ari_post(path):
    url = f"{ASTERISK_ARI_URL}/ari{path}"
    response = requests.post(
        url,
        auth=(ASTERISK_ARI_USER, ASTERISK_ARI_PASSWORD),
        timeout=10,
    )
    response.raise_for_status()

    if response.text:
        return response.json()

    return {}


async def handle_stasis_start(event):
    channel = event["channel"]
    channel_id = channel["id"]
    caller_number = channel.get("caller", {}).get("number", "unknown")
    args = event.get("args", [])

    language = args[0] if len(args) > 0 else "en"
    dialplan_caller = args[1] if len(args) > 1 else caller_number
    called_number = args[2] if len(args) > 2 else "unknown"

    print("=" * 70)
    print("[ARI] New call received")
    print(f"[ARI] Channel ID     : {channel_id}")
    print(f"[ARI] Caller Number  : {caller_number}")
    print(f"[ARI] Dialplan Caller: {dialplan_caller}")
    print(f"[ARI] Called Number  : {called_number}")
    print(f"[ARI] Language       : {language}")
    print("=" * 70)

    try:
        ari_post(f"/channels/{channel_id}/answer")
        print("[ARI] Channel answered")
    except Exception as e:
        print(f"[ARI] Answer failed or already answered: {e}")

    try:
        ari_post(f"/channels/{channel_id}/play?media=sound:hello-world")
        print("[ARI] Played hello-world prompt")
    except Exception as e:
        print(f"[ARI] Playback failed: {e}")

    print("[ARI] Current status: PBX + ARI control working")
    print("[ARI] Next step: connect this call to AI/OpenAI media bridge")


async def main():
    print(f"[ARI] Connecting to: {ARI_WS_URL}")

    async with websockets.connect(ARI_WS_URL) as ws:
        print("[ARI] Connected successfully")

        async for message in ws:
            event = json.loads(message)
            event_type = event.get("type")

            if event_type == "StasisStart":
                await handle_stasis_start(event)

            elif event_type == "StasisEnd":
                print("[ARI] Call ended")

            else:
                print(f"[ARI] Event received: {event_type}")


if __name__ == "__main__":
    asyncio.run(main())
