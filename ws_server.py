import asyncio
import functools
import json
from numbers import Number

import websockets

import jsonserver
from jsonserver import JSONDecodeError


class Socket:
    def __init__(self, ws):
        self.ws = ws

    async def send_message(self, message):
        """encode the message, then send it"""
        if message is None:
            return
        if isinstance(message, dict):
            message = json.dumps(message)
        if isinstance(message, Number):
            message = str(message)

        await self.ws.send(message)

    async def get_new_message(self):
        data = await self.ws.recv()
        try:
            return json.loads(data)
        except ValueError as e:
            raise JSONDecodeError(e, data)


class Context(jsonserver.Context):
    def __init__(self, *args, **kwargs):
        super(Context, self).__init__(*args, **kwargs)
        self.ws = self.writer


class WsServer(jsonserver.ExtendedServer):
    def __init__(self, *args, socket_obj=Socket, context_obj=Context, **kwargs):
        super(WsServer, self).__init__(*args, reader_obj=socket_obj, writer_obj=socket_obj,
                                       context_obj=context_obj,
                                       **kwargs)
        self.connection_error = websockets.exceptions.ConnectionClosed

    async def default_on_connect(self, ws, path):
        await super(WsServer, self).default_on_connect(ws, ws)

    def run(self, addr='127.0.0.1', port=8888, loop=None):
        """starts the asyncio server, dispatching on_connect when needed"""
        if loop is None:
            loop = asyncio.get_event_loop()

        callback = functools.partial(self.dispatch, 'connect')
        start_server = websockets.serve(callback, addr, port, loop=loop)
        coro = loop.run_until_complete(start_server)

        try:
            loop.run_forever()
        except KeyboardInterrupt:
            pass
        # cleanup, just to be safe
        coro.close()
        loop.run_until_complete(coro.wait_closed())
        loop.close()

    def command_(self, name):
        """a way to pass arguments to sever.command without breaking existing functionality"""
        def decorator(func):
            func.__name__ = name
            return self.command(func)
        return decorator
