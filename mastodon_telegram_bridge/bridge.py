import asyncio
import json
import logging
import os
from tempfile import TemporaryDirectory
from typing import Type
from urllib.parse import urlencode, urlsplit, urlunsplit

from mastodon import AttribAccessDict, CallbackStreamListener, Mastodon, MastodonAPIError
from mastodon.return_types import Status, try_cast_recurse
from telethon import TelegramClient, events
from telethon.helpers import add_surrogate, del_surrogate
from telethon.tl.custom.message import Message
from websockets.asyncio.client import connect

from .filter import Filter, MastodonFilter, TelegramFilter
from .footer import Footer, MastodonFooter, TelegramFooter
from .typing import (BridgeOptionsDict, MastodonOptionsDict, MastodonToTelegramOptions,
                     TelegramOptionsDict, TelegramToMastodonOptions)
from .utils import format_exception, markdownify


logger = logging.getLogger(__name__)


class Bridge:
    """The bridge between Mastodon and Telegram."""

    def __init__(self, *, telegram: TelegramOptionsDict, mastodon: MastodonOptionsDict,
                 options: BridgeOptionsDict, mastodon_filter: Type[Filter] = MastodonFilter,
                 telegram_filter: Type[Filter] = TelegramFilter,
                 mastodon_footer: Type[Footer] = MastodonFooter,
                 telegram_footer: Type[Footer] = TelegramFooter) -> None:
        self.mastodon = Mastodon(**mastodon)
        self.telegram = TelegramClient(
            telegram.get('session', 'mastodon-telegram-bridge'),
            telegram['api_id'], telegram['api_hash'],
        )
        self.bot_token = telegram['token']
        self._mastodon_username = self.mastodon.me().username
        self._mastodon_app_name = self.mastodon.app_verify_credentials().name
        self.mastodon_to_telegram = MastodonToTelegramOptions(**options['mastodon_to_telegram'])
        self.telegram_to_mastodon = TelegramToMastodonOptions(**options['telegram_to_mastodon'])
        if self.telegram_to_mastodon.disable and self.mastodon_to_telegram.disable:
            raise ValueError('Both telegram_to_mastodon and mastodon_to_telegram are disabled, nothing to do.')
        self.mastodon_filter = mastodon_filter(**self.telegram_to_mastodon.filter)
        self.telegram_filter = telegram_filter(**self.mastodon_to_telegram.filter)
        self.mastodon_footer = mastodon_footer(**self.telegram_to_mastodon.footer)
        self.telegram_footer = telegram_footer(**self.mastodon_to_telegram.footer)

    async def _report_error(self, chat_id: int, exc: Exception) -> None:
        logger.exception('Bridge error', exc_info=exc)
        try:
            await self.telegram.send_message(chat_id, format_exception(exc), parse_mode=None)
        except Exception:
            logger.exception('Could not report bridge error to Telegram')

    async def _wait_for_media_ready(self, media_ids: list[int]) -> None:
        wait_time = 1
        for _ in range(5):
            pending = []
            for media_id in media_ids:
                try:
                    await asyncio.to_thread(self.mastodon.media, media_id)
                except MastodonAPIError as exc:
                    if len(exc.args) > 1 and exc.args[1] == 206:
                        pending.append(media_id)
                    else:
                        raise
            if not pending:
                return
            await asyncio.sleep(wait_time)
            wait_time *= 2
        raise TimeoutError('Media is not ready after 5 retries')

    async def _send_media_to_mastodon(self, messages: list[Message]) -> None:
        cfg = self.telegram_to_mastodon
        caption_message = next((message for message in messages if message.raw_text), messages[0])
        text = caption_message.raw_text or ''
        if not self.mastodon_filter(text):
            return
        if len(messages) > 4:
            logger.warning('Too many media items: %d', len(messages))
            return
        media_ids = []
        with TemporaryDirectory(prefix='mastodon') as tmpdir:
            for message in messages:
                if not (message.photo or message.video):
                    logger.warning('Unsupported media type in album: %s', message.id)
                    continue
                file_path = os.path.join(tmpdir, str(message.id))
                downloaded = await self.telegram.download_media(message, file=file_path)
                if downloaded:
                    media = await asyncio.to_thread(self.mastodon.media_post, downloaded)
                    media_ids.append(media.id)
        if not media_ids:
            return
        await self._wait_for_media_ready(media_ids)
        text += f'\n\n{self.mastodon_footer(caption_message)}'
        status = await asyncio.to_thread(self.mastodon.status_post, text,
                                         content_type='text/markdown', media_ids=media_ids)
        await self._notify_success(caption_message, status.url, cfg.pm_chat_id)

    async def _notify_success(self, message: Message, url: str, pm_chat_id: int) -> None:
        text = f'Successfully forwarded to Mastodon.\n{url}'
        if message.is_group and message.fwd_from:
            await message.reply(text, parse_mode=None)
        else:
            await self.telegram.send_message(pm_chat_id, text, parse_mode=None)

    async def _send_message_to_mastodon(self, message: Message) -> None:
        cfg = self.telegram_to_mastodon
        try:
            if message.photo or message.video:
                await self._send_media_to_mastodon([message])
            elif message.raw_text:
                if not self.mastodon_filter(message.raw_text):
                    return
                text = add_surrogate(message.raw_text)
                for entity, entity_text in reversed(list(message.get_entities_text())):
                    if getattr(entity, 'url', None):
                        start = entity.offset
                        end = start + entity.length
                        text = text[:start] + f'[{add_surrogate(entity_text)}]({entity.url})' + text[end:]
                text = del_surrogate(text)
                text += f'\n\n{self.mastodon_footer(message)}'
                status = await asyncio.to_thread(self.mastodon.status_post, text,
                                                  content_type='text/markdown')
                await self._notify_success(message, status.url, cfg.pm_chat_id)
        except Exception as exc:
            await self._report_error(cfg.pm_chat_id, exc)

    async def _on_new_message(self, event: events.NewMessage.Event) -> None:
        if not event.out and not event.message.grouped_id:
            await self._send_message_to_mastodon(event.message)

    async def _on_album(self, event: events.Album.Event) -> None:
        if event.messages[0].out:
            return
        try:
            await self._send_media_to_mastodon(event.messages)
        except Exception as exc:
            await self._report_error(self.telegram_to_mastodon.pm_chat_id, exc)

    async def _send_message_to_telegram(self, status: AttribAccessDict) -> None:
        cfg = self.mastodon_to_telegram
        try:
            if not (status.account.username == self._mastodon_username and
                    (status.application is None or status.application.name != self._mastodon_app_name) and
                    self.telegram_filter(status)):
                return
            if status.reblog:
                if cfg.forward_reblog_link_only:
                    if status.reblog.visibility != 'public':
                        return
                    await self.telegram.send_message(cfg.channel_chat_id,
                                                     markdownify(self.telegram_footer(status.reblog)),
                                                     parse_mode='md', link_preview=False)
                    return
                status = status.reblog
            text = markdownify(status.content)
            if status.spoiler_text:
                text = f'**{status.spoiler_text}**\n\n{text}'
            text += '\n' + markdownify(self.telegram_footer(status))
            media_urls = [item.url for item in status.media_attachments
                          if item.type in ('image', 'video')]
            if media_urls:
                await self.telegram.send_file(cfg.channel_chat_id, media_urls,
                                              caption=text, parse_mode='md')
            else:
                await self.telegram.send_message(cfg.channel_chat_id, text,
                                                 parse_mode='md', link_preview=False)
        except Exception as exc:
            await self._report_error(cfg.pm_chat_id, exc)

    async def _stream_pleroma_user(self, queue: asyncio.Queue[AttribAccessDict], streaming_url: str) -> None:
        parsed = urlsplit(streaming_url)
        path = parsed.path.rstrip('/') or '/api/v1/streaming'
        query = urlencode({'stream': 'user', 'access_token': self.mastodon.access_token})
        url = urlunsplit((parsed.scheme, parsed.netloc, path, query, ''))
        while True:
            try:
                async with connect(url) as websocket:
                    logger.info('Connected to Pleroma/Akkoma user stream')
                    async for message in websocket:
                        event = json.loads(message)
                        if event.get('event') == 'update':
                            payload = event['payload']
                            status = json.loads(payload) if isinstance(payload, str) else payload
                            await queue.put(try_cast_recurse(Status, status))
            except Exception as exc:
                logger.warning('Pleroma/Akkoma user stream disconnected: %s',
                               str(exc).replace(self.mastodon.access_token, '[redacted]'))
            await asyncio.sleep(5)

    async def _run(self) -> None:
        await self.telegram.start(bot_token=self.bot_token)
        queue: asyncio.Queue[AttribAccessDict] = asyncio.Queue()
        stream = None
        stream_task = None
        consumer = None
        try:
            if not self.telegram_to_mastodon.disable:
                self.telegram.add_event_handler(
                    self._on_new_message,
                    events.NewMessage(chats=self.telegram_to_mastodon.channel_chat_id),
                )
                self.telegram.add_event_handler(
                    self._on_album,
                    events.Album(chats=self.telegram_to_mastodon.channel_chat_id),
                )
            if not self.mastodon_to_telegram.disable:
                instance = await asyncio.to_thread(self.mastodon.instance)
                if any(name in instance.version.lower() for name in ('pleroma', 'akkoma')):
                    stream_task = asyncio.create_task(
                        self._stream_pleroma_user(queue, instance.urls.streaming_api),
                    )
                else:
                    loop = asyncio.get_running_loop()
                    listener = CallbackStreamListener(
                        update_handler=lambda status: loop.call_soon_threadsafe(queue.put_nowait, status),
                    )
                    stream = await asyncio.to_thread(self.mastodon.stream_user, listener=listener,
                                                      run_async=True, reconnect_async=True)

                async def consume() -> None:
                    while True:
                        await self._send_message_to_telegram(await queue.get())

                consumer = asyncio.create_task(consume())
            await self.telegram.run_until_disconnected()
        finally:
            if stream_task:
                stream_task.cancel()
                await asyncio.gather(stream_task, return_exceptions=True)
            if consumer:
                consumer.cancel()
                await asyncio.gather(consumer, return_exceptions=True)
            if stream:
                await asyncio.to_thread(stream.close)
            await self.telegram.disconnect()

    def run(self, dry_run: bool = False) -> None:
        if dry_run:
            logger.info('Configuration valid; dry run complete.')
            return
        asyncio.run(self._run())
