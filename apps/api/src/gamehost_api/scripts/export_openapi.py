import json
import sys

from gamehost_api.main import app


def main() -> None:
    sys.stdout.write(json.dumps(app.openapi(), indent=2, sort_keys=True))
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
