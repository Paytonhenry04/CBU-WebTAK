#!/usr/bin/env python3
import asyncio, aiohttp
from adsb_tak import ADSB_URL

async def main():
    headers = {"User-Agent": "cbu-tak-demo"}
    async with aiohttp.ClientSession() as s:
        async with s.get(ADSB_URL, headers=headers, timeout=15) as r:
            data = await r.json()
    ac = data.get("aircraft") or data.get("ac", [])
    print(f"Total aircraft near KRAL: {len(ac)}")
    for a in ac:
        print(f"  reg={a.get('r')!r:12} hex={a.get('hex')} "
              f"flight={(a.get('flight') or '').strip()!r} "
              f"lat={a.get('lat')} lon={a.get('lon')}")

asyncio.run(main())
