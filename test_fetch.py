#!/usr/bin/env python3
"""test_fetch.py - check we can see CBU planes. No TAK needed."""
import asyncio, aiohttp
from adsb_tak import fetch_cbu_aircraft, CBU_TAILS

async def main():
    async with aiohttp.ClientSession() as s:
        hits = await fetch_cbu_aircraft(s)
    if not hits:
        print("No CBU aircraft airborne right now (normal if none flying).")
        print(f"Watching for: {', '.join(sorted(CBU_TAILS))}")
    for a in hits:
        print(f"{a.get('r')}  {(a.get('flight') or '').strip()}  "
              f"lat={a.get('lat')} lon={a.get('lon')} alt={a.get('alt_baro')}")

if __name__ == "__main__":
    asyncio.run(main())
