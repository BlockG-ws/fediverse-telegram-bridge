# fediverse-telegram-bridge

A simple telegram bot bridges (mastodon-api based) fediverse timeline.

[Example Config](config.example.toml)

## Changes in the fork
- Bump the dependencies (and code) to newer version compatible with python 3.13
- Fix bugs:
  - _version is not included in git
  - Markdown parse error when there is a '_' within the link
- Respect the default visibility settings
- Markdown support for pleroma-like instances

## TODO
- [] Handle message editing
