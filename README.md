# fediverse-telegram-bridge

A Telegram bot that bridges a Mastodon-compatible Fediverse timeline using Telethon.

[Example Config](config.example.toml)

## Setup

1. Create a Telegram bot with BotFather and get a bot token. Create a Telegram API application at [my.telegram.org/apps](https://my.telegram.org/apps) to get `api_id` and `api_hash`.
2. Copy `config.example.toml` to `config.toml` and fill in the Telegram and Mastodon credentials and chat IDs. Add the bot as an administrator to the configured channel or group. Send `/start` to the bot from the private chat configured as `pm_chat_id` so it can report errors.
3. Run `uv sync --locked`, then `uv run mastodon-telegram-bridge config.toml`. Use `uv run mastodon-telegram-bridge --version` to check the installation.

Telethon stores its authorization in the configured `session` path (default: `mastodon-telegram-bridge.session`). Keep this file private and persistent across restarts. If uv's global cache directory is not writable, set `UV_CACHE_DIR` to a writable directory before running `uv`.

The bridge stores message mappings in a separate SQLite database (`bridge.sqlite3` by default). Set `[storage].path` to change its location. Keep this file persistent and back it up along with the Telethon session. Existing forwards made before this database is created cannot be matched to later edits, deletions, or replies.

Forwarded messages are recorded by their Telegram chat/message IDs and Fediverse status ID, including album members. Repeated events and messages produced by the bridge are skipped. Telegram replies to mapped messages become Fediverse replies; edits to the original text or album caption update the matching post, and deleting any Telegram source message deletes the matching Fediverse post. Telegram edits and deletions require the bot to receive those events and the Fediverse server to support editing and deleting the post. If Telegram omits the chat ID from a deletion event, the bridge cannot identify the post to delete.

If channel posts are not received, check that the bot is a channel administrator. For group messages, disable bot privacy in BotFather if the bot needs to see all messages.

To append custom text to forwarded posts, set `text` under `[options.telegram_to_mastodon.footer]` or `[options.mastodon_to_telegram.footer]`. For example, `text = "Forwarded by my bridge"`. The default is an empty string, which adds no custom text.

To override the Fediverse account's default visibility for posts forwarded from Telegram, set `visibility = "unlisted"` under `[options.telegram_to_mastodon]`. Supported values are `public`, `unlisted`, `private`, and `direct`; omitting the setting keeps the account default.

Telegram text and media captions are converted from Telegram formatting to Fediverse Markdown, including bold, italic, strikethrough, code, quotes, and links. Formatting without a Markdown equivalent, such as underline and spoilers, is forwarded as plain text.

Fediverse HTML posts are forwarded to Telegram with their text formatting, links, and paragraph breaks preserved.

## Changes in the fork
- Use Telethon for Telegram messages and media albums.
- Keep `pyproject.toml`, `requirements.txt`, and `uv.lock` dependencies consistent.
- Use a static package version so `uv run` does not regenerate `_version.py`.
- Fix bugs:
  - Markdown parse error when there is a '_' within the link
- Respect the default visibility settings
- Markdown support for pleroma-like instances
- Receive Pleroma/Akkoma user updates through their WebSocket streaming API.

## Limitations
- A crash after a remote send succeeds but before its mapping is saved can leave a pending mapping. Fediverse post creation uses a stable idempotency key where the server supports it, but Telegram sends have no equivalent guarantee.
- Edits to media other than an album caption do not replace the corresponding Fediverse media.
