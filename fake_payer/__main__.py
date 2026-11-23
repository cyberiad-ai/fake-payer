"""Run the line:  python -m fake_payer   (configuration from the environment; a .env file in the working directory is read for anything not already set)."""
import asyncio
import logging
import os
from pathlib import Path

from .config import Config, ConfigError
from .prompts import Prompts
from .coverage import Fixture
from .refusals import Refusals
from .server import FakePayerServer
from .tree import PhoneTree


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


async def _run(config: Config) -> None:
    refusals = Refusals()
    await refusals.prepare(config)                      # the spoken refusals, from the cache or synthesized once
    fixture, tree, prompts = Fixture.load(), PhoneTree.load(), Prompts(config)
    server = FakePayerServer(config, refusals=refusals, tree=tree, prompts=prompts, fixture=fixture)

    async def keep_log_tidy() -> None:
        while True:
            removed = server.cleanup_log()
            if removed:
                logging.getLogger("fake_payer").info("removed %d call log entries past retention", removed)
            await asyncio.sleep(6 * 3600)

    tidy = asyncio.create_task(keep_log_tidy())
    preparing = None
    if config.line_mode != "conversation":
        # The tree's spoken lines (a couple of hundred) are made in the background, a few at a time, while the line already takes calls; one not ready when a caller needs it is
        # made then. Once made they are kept in AUDIO_CACHE_DIR, so only the first start after a change to the tree or fixture is slow.
        preparing = asyncio.create_task(prompts.prepare(tree.texts(fixture)))
    try:
        async with server.serve() as running:
            logging.getLogger("fake_payer").info("listening on %s:%s", config.listen_host, config.port)
            await running.serve_forever()
    finally:
        tidy.cancel()
        if preparing:
            preparing.cancel()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    _load_dotenv(Path(".env"))
    try:
        config = Config.from_env()
    except ConfigError as error:
        print(f"cannot start: {error}")
        return 2
    asyncio.run(_run(config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
