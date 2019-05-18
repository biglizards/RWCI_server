import functools

import sqlalchemy
from sqlalchemy import Column, Integer, String, orm
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from src.RWCI_server import ws_server

Base = declarative_base()


class Context(ws_server.Context):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = self.ws.user

    def get_user(self, **kwargs):
        return self.server.session.query(User).filter_by(**kwargs).first()


class Socket(ws_server.Socket):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = None


class User(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True)
    username = Column(String)
    password = Column(String)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sockets = []

    @orm.reconstructor
    def init_on_load(self):
        self.sockets = []

    async def send(self, message):
        for socket in self.sockets:
            await socket.send_message(message)


class Server(ws_server.WsServer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.engine = sqlalchemy.create_engine('sqlite:///rwci.db')
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.session = self.Session()

    def command(self, func):
        """
        When a command returns, if it did not specify a type,
        set that type to the name of the command.
        """

        @functools.wraps(func)
        async def wrapped_func(*args, **kwargs):
            return_value = await func(*args, **kwargs)
            if isinstance(return_value, dict) and self.command_field_name not in return_value:
                # since in python 3.7+ dicts are insertion ordered,
                # make a new dict so 'type' comes fist (for neatness)
                return_value = {self.command_field_name: func.__name__, **return_value}
            return return_value

        return super().command(wrapped_func)


server = Server(command_field_name='type', context_obj=Context, socket_obj=Socket)

online_users = {}  # username: User

channels = ['general', 'test', 'test2', 'spam']
general_channel = channels[0]


@server.event
def on_error(_ctx, e):
    print(e)
    raise e


@server.event
async def on_disconnect(ctx, _e):
    try:
        user = ctx.ws.user
        user.sockets.remove(ctx.ws)
        if len(user.sockets) == 0:
            del online_users[user.username]
            await send_quit_packet(user.username)
    except KeyError:
        pass


async def send_quit_packet(username):
    for user in online_users.values():
        await user.send({'type': 'quit', 'username': username})


def auth_user(ctx, username, password):
    if ctx.ws.user is not None:  # if the user already successfully authed this session, fail any further attempts
        return False, False, None

    user = ctx.get_user(username=username)
    new_account = user is None
    if new_account:
        user = User(username=username, password=password)
        ctx.server.session.add(user)
        ctx.server.session.commit()

    success = (user.password == password)
    return new_account, success, user


@server.command
async def auth(ctx, username, password):
    """Log a user in. If an account doesnt exist, make one."""
    new_account, success, user = auth_user(ctx, username, password)
    await ctx.send({'type': 'auth', 'new_account': new_account, 'success': success})

    if not success:
        return

    # handle internal stuff
    if username not in online_users:
        online_users[username] = user

    user.sockets.append(ctx.ws)
    ctx.ws.user = user  # permanently link this socket to the auth'd user

    # send out initial info, as per spec
    await ctx.send(await user_list(ctx))
    await ctx.send(await channel_list(ctx))
    await ctx.send(await default_channel(ctx))

    if len(user.sockets) == 1:
        for other_user in online_users.values():
            if other_user is user:
                continue
            await other_user.send({'type': 'join', 'username': username})


@server.command
async def user_list(_ctx):
    """returns all users who are online.
    uses `ctx.ws.user` since `ctx.user` is only set at context creation time, and this
    is sometimes (ie mostly) called by `auth()`"""

    # return {'users': list(user.username for user in online_users.values() if user is not ctx.ws.user)}
    return {'users': list(user.username for user in online_users.values())}


@server.command
async def channel_list(_ctx):
    return {'channels': channels}


@server.command
async def default_channel(_ctx):
    return {'channel': general_channel}


@server.command_(name='message')
async def message_(ctx, message, channel=default_channel, **_kwargs):
    for user in online_users.values():
        await user.send(
            {'type': 'message',
             'channel': channel,
             'author': ctx.user.username,
             'message': message}
        )


@server.command
async def direct_message(ctx, message, recipient, **_kwargs):
    await online_users[recipient].send(
        {
          "type": "direct_message",
          "author": ctx.user.username,
          "message": message
        }
    )


@server.command
async def typing(ctx, **_kwargs):
    for user in online_users.values():
        await user.send(
            {
                "type": "typing",
                "username": ctx.user.username
            }
        )

if __name__ == '__main__':
    server.run()
