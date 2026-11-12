import logging
from typing import Any, Iterable, Optional

from mastodon import AttribAccessDict
from telethon.tl.custom.message import Message
from telethon.tl.types import PeerChannel, PeerUser

logger = logging.getLogger(__name__)


class Footer:
    """The footer of a message 

    Allow user to add a custom footer to a message

    Inherit from this class to implement a footer for a specific platform

    Example:
        >>> class MyFooter(Footer):
        ...     def make_footer(self, message: Message) -> list[str]:
        ...        return [f'This is a footer for {message.id}', f'From {message.from_user.name}', f'Link: {message.link}']
        >>> footer = MyFooter()
        >>> footer(Message(id=1, from_user=User(name='foo'), link='https://example.com/1'))
        'This is a footer for 1\\nFrom foo\\nLink: https://example.com/1'
        >>> footer(Message(id=2, from_user=User(name='bar'), link='https://example.com/2'))
        'This is a footer for 2\\nFrom bar\\nLink: https://example.com/2'
    """

    def __init__(self, **kwargs) -> None:
        if kwargs:
            logger.warning(f'Unused arguments: {kwargs}')

    def _forwarded_from(self, name: str) -> str:
        return f'Forwarded from {name}'

    def make_footer(self, _: Message | AttribAccessDict) -> list[str]:
        raise NotImplementedError

    def __call__(self, message: Message | AttribAccessDict) -> str:
        footer = self.make_footer(message)
        return '\n'.join(footer)


class MastodonFooter(Footer):
    """Footer for mastodon statuses
    """

    def __init__(self, *, add_link: bool, show_forward_from: bool, text: str = '', **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.add_link = add_link
        self.show_forward_from = show_forward_from
        self.text = text

    def __call__(self, message: Message) -> str:
        return '\n\n'.join(self.make_footer(message))

    def __get_forward_name(self, message: Message) -> Optional[str]:
        origin = message.fwd_from
        if not origin:
            return None
        if origin.from_name:
            return origin.from_name
        if isinstance(origin.from_id, PeerChannel):
            return f'channel {origin.from_id.channel_id}'
        if isinstance(origin.from_id, PeerUser):
            return f'user {origin.from_id.user_id}'
        return None

    def __get_forward_link(self, message: Message) -> Optional[str]:
        origin = message.fwd_from
        if origin and isinstance(origin.from_id, PeerChannel) and origin.channel_post:
            return f'https://t.me/c/{origin.from_id.channel_id}/{origin.channel_post}'
        return None

    def make_footer(self, message: Message) -> list[str]:
        """generate footer

        Args:
            message (Message): the message from telegram

        Returns:
            List[str]: footer lines in list
        """
        footer = []
        if self.show_forward_from:
            if forward_from := self.__get_forward_name(message):
                footer.append(self._forwarded_from(forward_from))
            if forward_link := self.__get_forward_link(message):
                footer.append(forward_link)
        if self.add_link:
            chat = message.chat
            if chat and getattr(chat, 'username', None):
                footer.append(f'https://t.me/{chat.username}/{message.id}')
            elif message.chat_id and str(message.chat_id).startswith('-100'):
                footer.append(f'https://t.me/c/{str(message.chat_id)[4:]}/{message.id}')
        if self.text:
            footer.append(self.text)
        return footer


class TelegramFooter(Footer):
    """Footer for telegram messages
    """

    def __init__(self, *, add_link: bool, tags: Iterable[str], text: str = '', **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.add_link = add_link
        self.tags = tags
        self.text = text
        self.__check_tags()

    def __check_tags(self) -> None:
        if not isinstance(self.tags, Iterable):
            raise TypeError(f'tags must be an iterable, got {type(self.tags)}')
        if not all(isinstance(tag, str) for tag in self.tags):
            raise TypeError(f'tags must be an iterable of str, got {type(self.tags)}')
        if not all(tag.startswith('#') for tag in self.tags):
            raise ValueError(f'tags must start with #, got {self.tags}')

    def make_footer(self, status: AttribAccessDict) -> list[str]:
        """generate footer

        Args:
            status (AttribAccessDict): the status dict from mastodon

        Returns:
            List[str]: footer lines in list
        """
        footer = []
        if self.add_link:
            footer.append(status.url)
        if self.tags:
            footer.append(' '.join(f'{tag}' for tag in self.tags))
        if self.text:
            footer.append(self.text)
        return footer
