from typing import Iterable, NamedTuple, Type, TypedDict

from .filter import Filter
from .footer import Footer


class OptionsDict(TypedDict):
    """Options Dict
    """
    pass


class CustomArgs(TypedDict, total=False):
    """Custom Args
    """
    mastodon_filter: Type[Filter]
    mastodon_footer: Type[Footer]
    telegram_filter: Type[Filter]
    telegram_footer: Type[Footer]


class TelegramRequiredOptionsDict(TypedDict):
    """TelegramOptionsDict
    """
    token: str
    api_id: int
    api_hash: str


class TelegramOptionsDict(TelegramRequiredOptionsDict, total=False):
    session: str


class MastodonOptionsDict(TypedDict):
    """MastodonOptionsDict
    """
    api_base_url: str
    access_token: str


class MastodonToTelegramFilterOptionsDict(TypedDict):
    """Mastodon To Telegram Filter Options Dict
    """
    scope: Iterable[str]


class MastodonToTelegramFooterOptionsDict(TypedDict):
    """Mastodon To Telegram Footer Options Dict
    """
    add_link: bool
    tags: Iterable[str]
    text: str


class MastodonToTelegramOptionsDict(TypedDict):
    """Mastodon To Telegram Options Dict
    """
    disable: bool
    channel_chat_id: int
    pm_chat_id: int
    forward_reblog_link_only: bool
    filter: OptionsDict
    footer: OptionsDict


class TelegramToMastodonFooterOptionsDict(TypedDict):
    """Telegram To Mastodon Footer Options Dict
    """
    add_link: bool
    show_forward_from: bool
    text: str


class TelegramToMastodonFilterOptionsDict(TypedDict):
    """Telegram To Mastodon Filter Options Dict
    """
    include: Iterable[str]
    exclude: Iterable[str]


class TelegramToMastodonOptionsDict(TypedDict, total=False):
    """Telegram To Mastodon Options Dict
    """
    disable: bool
    channel_chat_id: int
    pm_chat_id: int
    visibility: str
    filter: OptionsDict
    footer: OptionsDict


class BridgeOptionsDict(TypedDict):
    """Bridge Options Dict
    """
    telegram_to_mastodon: TelegramToMastodonOptionsDict
    mastodon_to_telegram: MastodonToTelegramOptionsDict


# Config Dict Types loaded from config.toml
class ConfigDict(TypedDict):
    """Config Dict
    """
    telegram: TelegramOptionsDict
    mastodon: MastodonOptionsDict
    options: BridgeOptionsDict


# Bridge Options


class MastodonToTelegramOptions(NamedTuple):
    """Mastodon To Telegram Options
    """
    disable: bool = False
    channel_chat_id: int = 0
    pm_chat_id: int = 0
    forward_reblog_link_only: bool = True
    filter: OptionsDict = MastodonToTelegramFilterOptionsDict(
        scope=['public', 'unlisted'],
    )
    footer: OptionsDict = MastodonToTelegramFooterOptionsDict(
        add_link=True,
        tags=['#mastodon'],
        text='',
    )


class TelegramToMastodonOptions(NamedTuple):
    """Telegram To Mastodon Options
    """
    disable: bool = False
    channel_chat_id: int = 0
    pm_chat_id: int = 0
    visibility: str | None = None
    filter: OptionsDict = TelegramToMastodonFilterOptionsDict(
        include=[],
        exclude=['#nofwd', '#noforward', '#mastodon'],
    )
    footer: OptionsDict = TelegramToMastodonFooterOptionsDict(
        add_link=False,
        show_forward_from=True,
        text='',
    )
