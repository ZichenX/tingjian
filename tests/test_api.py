import io
import json
import wave
import numpy as np
import pytest
from starlette.websockets import WebSocketDisconnect
from app.audio import wav_bytes
from tests.conftest import CODE


def test_public_ui_and_security_headers(client):
    response=client.get('/');assert response.status_code==200 and '把话看清楚' in response.text
    assert 'frame-ancestors' in response.headers['content-security-policy']
    assert response.headers['cache-control']=='no-store'
    assert client.get('/assets/app.js').status_code==200
    assert client.get('/health/ready').json()=={'ready':True}
    assert client.get('/api/session').status_code==401
    assert client.get('/docs').status_code==404
    assert client.get('/',headers={'host':'evil.test'}).status_code==400


def test_login_origin_and_body(client,settings):
    assert client.post('/api/login',json={'code':CODE}).status_code==403
    assert client.post('/api/login',headers={'Origin':'https://evil.test'},json={'code':CODE}).status_code==403
    h={'Origin':settings.origin}
    assert client.post('/api/login',headers=h,json={'code':'wrong'}).status_code==401
    assert client.post('/api/login',headers=h,content='not json').status_code==415
    assert client.post('/api/login',headers=h,json={'code':'x'*2000}).status_code==400
    response=client.post('/api/login',headers=h,json={'code':CODE})
    assert response.status_code==200
    cookie=response.headers['set-cookie'];assert 'HttpOnly' in cookie and 'SameSite=strict' in cookie


def test_session_and_csrf(signed):
    client,headers=signed
    assert client.get('/api/session').json()['tts'] is True
    assert client.post('/api/tts',json={'text':'你好'}).status_code==403
    assert client.post('/api/tts',headers={**headers,'X-CSRF-Token':'bad'},json={'text':'你好'}).status_code==403
    assert client.post('/api/logout',headers=headers).status_code==200
    assert client.get('/api/session').status_code==401


@pytest.mark.parametrize('payload',[{'text':''},{'text':'字'*121},{'text':'!!!'},{'text':'你好','speed':0},{'text':'你好','speed':True},{'text':[]}])
def test_tts_validation(signed,payload):
    client,headers=signed;assert client.post('/api/tts',headers=headers,json=payload).status_code==400


def test_tts_binary_wav(signed):
    client,headers=signed;response=client.post('/api/tts',headers=headers,json={'text':'你好','speed':.85})
    assert response.status_code==200 and response.headers['content-type']=='audio/wav'
    with wave.open(io.BytesIO(response.content)) as audio:assert audio.getnchannels()==1 and audio.getnframes()>0


def test_upload_auth_limits_and_bad_audio(client,signed):
    client,headers=signed
    assert client.post('/api/transcribe',content=b'x').status_code==403
    assert client.post('/api/transcribe',headers=headers,content=b'').status_code==400
    assert client.post('/api/transcribe',headers=headers,content=b'not audio').status_code==400
    assert client.post('/api/transcribe',headers=headers,content=b'x'*(1024*1024+1)).status_code==413
    assert client.app.state.capacity.active=={}


def test_upload_real_decoder_fake_recognizer(signed):
    client,headers=signed
    samples=.15*np.sin(np.arange(16000*3)*2*np.pi*440/16000)
    response=client.post('/api/transcribe',headers=headers,content=wav_bytes(samples,16000))
    assert response.status_code==200
    events=[json.loads(line) for line in response.text.splitlines()]
    assert events[0]['type']=='progress' and events[-1]['ok'] is True
    finals=[e for e in events if e['type']=='final'];assert len(finals)==2
    assert finals[0]['id']==0 and finals[1]['id']==1 and '界面测试' in finals[0]['text']
    assert client.app.state.capacity.active=={}


def test_upload_silence(signed):
    client,headers=signed
    response=client.post('/api/transcribe',headers=headers,content=wav_bytes(np.zeros(16000),16000))
    assert json.loads(response.text.splitlines()[-1])['segments']==0


def test_websocket_requires_auth_and_origin(client,settings):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect('ws://localhost:8000/api/live',headers={'Origin':settings.origin}):pass


def test_websocket_invalid_csrf(signed,settings):
    client,h=signed
    with client.websocket_connect('ws://localhost:8000/api/live',headers={'Origin':settings.origin}) as ws:
        ws.send_json({'type':'start','csrf':'bad','sample_rate':16000})
        assert ws.receive_json()['type']=='error'


def test_websocket_pcm_and_stop_flush(signed,settings):
    client,h=signed
    with client.websocket_connect('ws://localhost:8000/api/live',headers={'Origin':settings.origin}) as ws:
        ws.send_json({'type':'start','csrf':h['X-CSRF-Token'],'sample_rate':48000})
        assert ws.receive_json()['type']=='ready'
        pcm=(np.sin(np.arange(48000)*2*np.pi*440/48000)*10000).astype('<i2')
        for start in range(0,len(pcm),2048):ws.send_bytes(pcm[start:start+2048].tobytes())
        ws.send_json({'type':'stop'})
        events=[]
        while True:
            event=ws.receive_json();events.append(event)
            if event['type']=='done':break
        assert events[-1]['ok'] is True
        assert len([x for x in events if x['type']=='final'])==1
    assert client.app.state.capacity.active=={}


def test_websocket_reject_accelerated_audio(signed,settings):
    client,h=signed
    with client.websocket_connect('ws://localhost:8000/api/live',headers={'Origin':settings.origin}) as ws:
        ws.send_json({'type':'start','csrf':h['X-CSRF-Token'],'sample_rate':16000})
        assert ws.receive_json()['type']=='ready'
        # Binary frame is strictly bounded, so this fails before allocating a giant audio buffer.
        ws.send_bytes(b'\x00'*16002)
        assert ws.receive_json()['type']=='error'
