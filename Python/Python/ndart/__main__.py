import argparse
import json
import signal

from .config import defaults, load, save
from .engine import Engine


def main():
    parser = argparse.ArgumentParser(description="N-Dart configurable acquisition and telemetry")
    parser.add_argument("--headless", action="store_true", help="Run without the desktop window")
    parser.add_argument("--config", help="Saved JSON settings profile")
    parser.add_argument("--seconds", type=float, help="Override run duration (0 means until interrupted)")
    parser.add_argument("--output", help="Override data output directory")
    parser.add_argument("--write-defaults", metavar="PATH", help="Write a default settings profile and exit")
    args = parser.parse_args()
    if args.write_defaults:
        save(args.write_defaults, defaults())
        return 0
    if not args.headless:
        if args.config or args.seconds is not None or args.output:
            parser.error("Use --headless with --config, --seconds and --output; the desktop has Load profile")
        from .gui import launch
        launch()
        return 0
    config = load(args.config) if args.config else defaults()
    if args.seconds is not None:
        config["duration_seconds"] = args.seconds
    if args.output:
        config["output_dir"] = args.output

    def report(event):
        if event["kind"] != "status":
            print(json.dumps(event, ensure_ascii=True), flush=True)

    engine = Engine(config, report)
    signal.signal(signal.SIGINT, lambda *_: engine.stop())
    signal.signal(signal.SIGTERM, lambda *_: engine.stop())
    return int(engine.run() is not None)


if __name__ == "__main__":
    raise SystemExit(main())
