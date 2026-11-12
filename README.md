# fediverse-telegram-bridge

A Telegram bot that bridges a Mastodon-compatible Fediverse timeline using Telethon.

[Example Config](config.example.toml)

## Setup

1. Create a Telegram bot with BotFather and get a bot token. Create a Telegram API application at [my.telegram.org/apps](https://my.telegram.org/apps) to get `api_id` and `api_hash`.
2. Copy `config.example.toml` to `config.toml` and fill in the Telegram and Mastodon credentials and chat IDs. Add the bot as an administrator to the configured channel or group. Send `/start` to the bot from the private chat configured as `pm_chat_id` so it can report errors.
3. Run `uv sync --locked`, then `uv run mastodon-telegram-bridge config.toml`. Use `uv run mastodon-telegram-bridge --version` to check the installation.

Telethon stores its authorization in the configured `session` path (default: `mastodon-telegram-bridge.session`). Keep this file private and persistent across restarts. If uv's global cache directory is not writable, set `UV_CACHE_DIR` to a writable directory before running `uv`.

If channel posts are not received, check that the bot is a channel administrator. For group messages, disable bot privacy in BotFather if the bot needs to see all messages.

To append custom text to forwarded posts, set `text` under `[options.telegram_to_mastodon.footer]` or `[options.mastodon_to_telegram.footer]`. For example, `text = "Forwarded by my bridge"`. The default is an empty string, which adds no custom text.

## Changes in the fork
- Use Telethon for Telegram messages and media albums.
- Keep `pyproject.toml`, `requirements.txt`, and `uv.lock` dependencies consistent.
- Use a static package version so `uv run` does not regenerate `_version.py`.
- Fix bugs:
  - Markdown parse error when there is a '_' within the link
- Respect the default visibility settings
- Markdown support for pleroma-like instances
- Receive Pleroma/Akkoma user updates through their WebSocket streaming API.

## TODO
- [ ] Handle message editing, replies & deletion
- [ ] Allow to change visibility settings in config files
