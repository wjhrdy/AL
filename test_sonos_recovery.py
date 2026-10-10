import ast
import asyncio
import logging
import time
from unittest.mock import Mock
from pathlib import Path

source = ast.parse(Path('hello.py').read_text())
cls = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == 'MusicIdentifier')
methods = [n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in ('init_sonos', 'get_sonos_track_info')]
module = ast.Module(body=[ast.ClassDef(name='Recovery', bases=[], keywords=[], body=methods, decorator_list=[])], type_ignores=[])
ns = dict(asyncio=asyncio, time=time, discover=Mock(), logging=logging)
exec(compile(ast.fix_missing_locations(module), '<recovery>', 'exec'), ns)

def app():
    a = ns['Recovery']()
    a.logger = Mock()
    a.sonos_speaker = None
    a.sonos_player_name = None
    a.sonos_is_playing = False
    a.sonos_failures = 0
    a.last_sonos_check = 0
    a.sonos_check_interval = 1
    a.sonos_check_interval_paused = 5
    a.sonos_retry_interval = 30
    a.last_sonos_discovery = None
    a.last_identified = {'title':'old', 'artist':'old'}
    return a

def speaker(name='Lounge'):
    s = Mock()
    s.player_name = name
    s.get_current_transport_info.return_value = {'current_transport_state':'PLAYING'}
    s.get_current_track_info.return_value = {'title':'New track', 'artist':'Artist', 'album_art':'http://art'}
    return s

async def test():
    d = ns['discover']
    a = app()
    d.return_value = None
    assert await a.get_sonos_track_info() is None
    count = d.call_count
    assert await a.get_sonos_track_info() is None
    assert d.call_count == count, 'Discovery should be throttled'
    a.last_sonos_discovery -= 31
    good = speaker()
    d.return_value = {good}
    track = await a.get_sonos_track_info()
    assert track['title'] == 'New track' and a.last_identified is None
    print('PASS: startup discovery failure, throttled retry, and recovery')
    good.get_current_transport_info.side_effect = OSError('speaker unreachable')
    for _ in range(3):
        a.last_sonos_check = 0
        assert await a.get_sonos_track_info() is None
    assert a.sonos_speaker is None
    good.get_current_transport_info.side_effect = None
    assert (await a.get_sonos_track_info())['title'] == 'New track'
    assert a.sonos_failures == 0
    print('PASS: repeated polling failures trigger rediscovery and recover')
    a.sonos_speaker = None
    a.last_sonos_discovery = None
    d.return_value = {speaker('Other room')}
    assert await a.get_sonos_track_info() is None
    assert a.sonos_speaker is None
    print('PASS: reconnect does not switch to a different speaker')
    a = app()
    d.side_effect = OSError('network down')
    assert await a.get_sonos_track_info() is None
    a.logger.exception.assert_called()
    print('PASS: discovery exceptions are logged and remain retryable')

asyncio.run(test())
