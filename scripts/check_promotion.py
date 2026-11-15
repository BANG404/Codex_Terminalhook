"""Enforce alpha -> beta -> rc -> main promotion PRs."""
import json
import os
from pathlib import Path


def validate(base, head, same_repository):
    source = {"beta": "alpha", "rc": "beta", "main": "rc"}.get(base)
    if source and (head != source or not same_repository):
        raise ValueError(f"Promotion to {base} requires a same-repository {source} PR")


def main():
    if os.environ.get("GITHUB_EVENT_NAME") != "pull_request":
        print("No promotion PR to validate")
        return
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    pr = event["pull_request"]
    validate(pr["base"]["ref"], pr["head"]["ref"], pr["head"]["repo"]["full_name"] == pr["base"]["repo"]["full_name"])
    print("Promotion policy verified")


if __name__ == "__main__":
    main()
