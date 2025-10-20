# fediverse-telegram-bridge

A simple telegram bot bridges (mastodon-api based) fediverse timeline.

[Example Config](config.example.toml)

_Some bots may encounter not receiving messages from channels due to unknown reasons._ 

_ If you encounter such issue, try to create a new bot with group privacy disabled and set it as admin to fully manage messages._

## Changes in the fork
- Bump the dependencies (and code) to newer version compatible with python 3.13
- Fix bugs:
  - _version is not included in git
  - Markdown parse error when there is a '_' within the link
- Respect the default visibility settings
- Markdown support for pleroma-like instances

## TODO
- [] Handle message editing
