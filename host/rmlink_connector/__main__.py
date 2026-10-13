import argparse
import asyncio
import json
import logging
import os

from .client import ApiClient
from .service import Connector
from .state import State


async def serve(args):
    logging.basicConfig(level=logging.INFO if os.environ.get('RMLINK_USBIP_TRACE') == '1' else logging.WARNING)
    loop = asyncio.get_running_loop()
    def loop_error(loop, context):
        exc = context.get('exception')
        callback = getattr(context.get('handle'), '_callback', None)
        # Python 3.11 Proactor shutdown can report a second reset while cleaning
        # up an already-disconnected socket. The broker separately reports loss.
        if (isinstance(exc, ConnectionResetError) and getattr(exc, 'winerror', None) == 10054
                and getattr(callback, '__name__', '') == '_call_connection_lost'):
            logging.info('Peer reset during socket cleanup')
        else:
            loop.default_exception_handler(context)
    loop.set_exception_handler(loop_error)
    connector = Connector(State(args.state_dir))
    try:
        port = await connector.start(args.api_port, args.usbip_port)
        print(json.dumps({'ready': True, 'api_port': port, 'usbip_port': connector.usbip_port}), flush=True)
        await connector.stopping.wait()
    finally: await connector.close()


def main():
    parser = argparse.ArgumentParser(description='RMLink connection service development tool')
    parser.add_argument('--state-dir')
    parser.add_argument('--api-port', type=int, default=3243)
    parser.add_argument('--usbip-port', type=int, default=3240)
    parser.add_argument('command', choices=['serve', 'status', 'devices', 'pair', 'mount', 'unmount', 'stop'])
    parser.add_argument('device_id', nargs='?')
    args = parser.parse_args()
    if args.command == 'serve':
        try: asyncio.run(serve(args))
        except KeyboardInterrupt: pass
        return
    client = ApiClient(args.state_dir, args.api_port)
    if args.command in ('status', 'devices'):
        result = client.request('GET', '/v1/' + args.command)
    elif args.command == 'stop': result = client.request('POST', '/v1/shutdown')
    else:
        if not args.device_id:
            devices = client.request('GET', '/v1/devices')
            if len(devices) != 1: parser.error('Choose a discovered device_id when multiple devices are present')
            args.device_id = devices[0]['device_id']
        result = client.request('POST', '/v1/' + args.command, {'device_id': args.device_id})
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
