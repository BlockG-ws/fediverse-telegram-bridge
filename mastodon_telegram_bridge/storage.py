from pathlib import Path

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


class Base(DeclarativeBase):
    pass


class BridgedPost(Base):
    __tablename__ = 'bridged_posts'
    __table_args__ = (UniqueConstraint('instance', 'status_id'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    instance: Mapped[str] = mapped_column(String, nullable=False)
    status_id: Mapped[str | None] = mapped_column(String)
    origin: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False, default='pending')
    primary_message_id: Mapped[int | None] = mapped_column(Integer)
    telegram_messages: Mapped[list['TelegramMessage']] = relationship(back_populates='post', cascade='all, delete-orphan')


class TelegramMessage(Base):
    __tablename__ = 'telegram_messages'
    __table_args__ = (UniqueConstraint('chat_id', 'message_id'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey('bridged_posts.id'), nullable=False)
    chat_id: Mapped[int] = mapped_column(Integer, nullable=False)
    message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    post: Mapped[BridgedPost] = relationship(back_populates='telegram_messages')


class Storage:
    def __init__(self, path: str, instance: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(f'sqlite:///{Path(path).resolve().as_posix()}')
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine)
        self.instance = instance.rstrip('/')

    def telegram_post(self, chat_id: int, message_id: int) -> BridgedPost | None:
        with self.sessions() as session:
            return session.scalar(select(BridgedPost).join(TelegramMessage).where(
                TelegramMessage.chat_id == chat_id, TelegramMessage.message_id == message_id))

    def telegram_posts_by_message_id(self, message_id: int) -> list[BridgedPost]:
        with self.sessions() as session:
            return list(session.scalars(select(BridgedPost).join(TelegramMessage).where(
                TelegramMessage.message_id == message_id)).all())

    def fediverse_post(self, status_id: str) -> BridgedPost | None:
        with self.sessions() as session:
            return session.scalar(select(BridgedPost).where(
                BridgedPost.instance == self.instance, BridgedPost.status_id == str(status_id)))

    def post_messages(self, post_id: int) -> list[tuple[int, int]]:
        with self.sessions() as session:
            return list(session.execute(select(TelegramMessage.chat_id, TelegramMessage.message_id).where(
                TelegramMessage.post_id == post_id)).all())

    def sent_telegram_messages(self, chat_id: int) -> list[int]:
        with self.sessions() as session:
            return list(session.scalars(select(TelegramMessage.message_id).join(BridgedPost).where(
                TelegramMessage.chat_id == chat_id,
                BridgedPost.origin == 'telegram', BridgedPost.state == 'sent')).all())

    def reserve_telegram(self, messages: list[tuple[int, int]]) -> int | None:
        with self.sessions.begin() as session:
            for chat_id, message_id in messages:
                if session.scalar(select(TelegramMessage.id).where(
                        TelegramMessage.chat_id == chat_id, TelegramMessage.message_id == message_id)):
                    return None
            post = BridgedPost(instance=self.instance, origin='telegram', state='pending',
                               primary_message_id=messages[0][1])
            post.telegram_messages = [TelegramMessage(chat_id=chat_id, message_id=message_id)
                                      for chat_id, message_id in messages]
            session.add(post)
            session.flush()
            return post.id

    def reserve_fediverse(self, status_id: str) -> int | None:
        if self.fediverse_post(status_id):
            return None
        with self.sessions.begin() as session:
            post = BridgedPost(instance=self.instance, status_id=str(status_id),
                               origin='fediverse', state='pending')
            session.add(post)
            session.flush()
            return post.id

    def complete_telegram(self, post_id: int, status_id: str) -> None:
        with self.sessions.begin() as session:
            post = session.get(BridgedPost, post_id)
            post.status_id = str(status_id)
            post.state = 'sent'

    def complete_fediverse(self, post_id: int, messages: list[tuple[int, int]]) -> None:
        with self.sessions.begin() as session:
            post = session.get(BridgedPost, post_id)
            post.telegram_messages = [TelegramMessage(chat_id=chat_id, message_id=message_id)
                                      for chat_id, message_id in messages]
            post.state = 'sent'

    def mark_deleted(self, post_id: int) -> None:
        with self.sessions.begin() as session:
            session.get(BridgedPost, post_id).state = 'deleted'

    def release(self, post_id: int) -> None:
        with self.sessions.begin() as session:
            post = session.get(BridgedPost, post_id)
            if post and post.state == 'pending':
                session.delete(post)
