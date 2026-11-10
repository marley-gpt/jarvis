"""A wake worker whose first start fails on the mic, like a WDM-KS hiccup at sign-in; later starts work."""
import pathlib
import sys

marker = pathlib.Path(sys.argv[1])
if not marker.exists():
    marker.write_text("failed once", encoding="utf-8")
    print("ERROR PortAudioError: Blocking API not supported yet", flush=True)
    sys.exit(1)
print("READY stt", flush=True)
for line in sys.stdin:
    if line.strip() == "quit":
        break
