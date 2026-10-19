import argparse
import asyncio
import json
import logging

from .client import ApiClient
from .service import Connector
from .state import State
from .recording_reader import inspect_recording
from .recording_audit import audit_recording


async def serve(args):
    logging.basicConfig(level=logging.WARNING)
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
        port = await connector.start(args.api_port)
        print(json.dumps({'ready': True, 'api_port': port}), flush=True)
        await connector.stopping.wait()
    finally: await connector.close()


def main():
    parser = argparse.ArgumentParser(description='RMLink connection service development tool')
    parser.add_argument('--state-dir')
    parser.add_argument('--api-port', type=int, default=3243)
    parser.add_argument('command', choices=['serve', 'status', 'devices', 'pair', 'stop','inspect-recording'])
    parser.add_argument('device_id', nargs='?')
    parser.add_argument('--anomalies',action='store_true',help='Offline sample validity and sequence diagnostics for inspect-recording')
    args = parser.parse_args()
    if args.command=='inspect-recording':
        if not args.device_id:parser.error('inspect-recording requires a file path')
        try:result=audit_recording(args.device_id) if args.anomalies else inspect_recording(args.device_id)
        except (OSError,ValueError,KeyError) as error:parser.error(str(error))
        print(json.dumps(result,ensure_ascii=False,indent=2));return
    if args.anomalies:parser.error('--anomalies requires inspect-recording')
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
