import asyncio
import io
import json
import tarfile
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
from app.audio import AudioResampler, AudioError, decode_upload, wav_bytes, pcm16_to_float
from app.catalog import selected
from app.config import Settings
from app.engine import clean_text
from app.security import Auth, RateLimiter, Capacity, verify_code
from app.segmenter import Segmenter
from app.worker import InferenceWorker, BusyError
from scripts.download_models import safe_extract, file_manifest, verify_manifest, atomic_json
from scripts.evaluate import distance, normalize
from scripts.configure import load_env, write_env

@pytest.mark.parametrize('mode,tts,count',[('dual',True,4),('dual',False,3),('aed-only',False,2),('ctc-only',True,3),('sensevoice',True,3),('funasr-nano',True,3)])
def test_model_selection(mode,tts,count): assert len(selected(mode,tts)) == count

@pytest.mark.parametrize('change',[{'origin':'http://example.com'}, {'origin':'https://a.com/path'}, {'secret':'short'}, {'code_hash':''}, {'asr_mode':'invented'}, {'threads':0}, {'max_live':0}, {'silence':.1}, {'segment_seconds':60}, {'max_upload_mb':500}, {'inference_timeout':0}, {'session_seconds':0}, {'cookie_days':999}])
def test_invalid_settings(settings,change):
    with pytest.raises(ValueError): replace(settings,**change).validate()

def test_hash(password_hash):
    assert verify_code('test-only-code-123',password_hash)
    assert not verify_code('wrong',password_hash)
    assert not verify_code('a','pbkdf2_sha256:99999999:x:x')

def test_session_security(monkeypatch):
    auth=Auth('x'*40); token,session=auth.issue()
    assert auth.read(token)==session
    assert auth.read(token[:-1]+'!') is None
    assert Auth('y'*40).read(token) is None
    assert auth.csrf(session)!=auth.csrf(auth.issue()[1])
    monkeypatch.setattr('app.security.time.time', lambda: session.exp+1)
    assert auth.read(token) is None

@pytest.mark.parametrize('token',[None,'','bad','a.b.c','a.b','x'*1025])
def test_bad_cookies(token): assert Auth('x'*40).read(token) is None

def test_limits(monkeypatch):
    now=[0.];monkeypatch.setattr('app.security.time.monotonic',lambda:now[0])
    rate=RateLimiter(2)
    assert rate.allow('a',1,10); assert not rate.allow('a',1,10)
    now[0]=11;assert rate.allow('a',1,10)
    rate.allow('b',1,10);rate.allow('c',1,10);assert len(rate.entries)==2
    cap=Capacity(2);assert cap.acquire('a','ip1','live');assert not cap.acquire('b','ip1','live')
    assert cap.acquire('b','ip2','live');assert not cap.acquire('c','ip3','live')
    cap.release('a');assert cap.acquire('a','ip1','tts');assert not cap.acquire('d','ip4','tts')

@pytest.mark.parametrize('rate',[8000,16000,44100,48000,96000])
def test_streaming_resample(rate):
    samples=(np.sin(np.arange(rate*2)*2*np.pi*440/rate)*20000).astype('<i2')
    resampler=AudioResampler(rate);out=[]
    for start in range(0,len(samples),2048):out.append(resampler.feed(samples[start:start+2048].tobytes()))
    out.append(resampler.feed(b'',last=True));out=np.concatenate(out)
    assert abs(len(out)-32000)<=1 and np.isfinite(out).all()
    assert abs(float(np.sqrt(np.mean(out**2)))-.4315)<.025

@pytest.mark.parametrize('rate',[True,None,0,7999,96001,'48000'])
def test_bad_samplerates(rate):
    with pytest.raises(AudioError):AudioResampler(rate)

def test_bad_pcm():
    with pytest.raises(AudioError):pcm16_to_float(b'1')

async def test_real_ffmpeg(tmp_path):
    path=tmp_path/'input.wav';samples=.2*np.sin(np.arange(32000)*2*np.pi*440/16000)
    path.write_bytes(wav_bytes(samples,16000))
    actual=await decode_upload(path,10)
    assert len(actual)==32000 and np.max(np.abs(actual-samples))<.0001

async def test_ffmpeg_duration_limit(tmp_path):
    path=tmp_path/'input.wav';path.write_bytes(wav_bytes(np.ones(16000*11)*.1,16000))
    with pytest.raises(AudioError,match='超过'):await decode_upload(path,10)

async def test_ffmpeg_reject_bad_file(tmp_path):
    path=tmp_path/'bad.wav';path.write_text('not audio')
    with pytest.raises(AudioError):await decode_upload(path,10)

def test_text_normalization():
    assert clean_text('<|zh|><|NEUTRAL|>俺来了\x00')=='俺来了'
    assert normalize('ＡＢＣ， 俺来了！')=='abc俺来了'
    assert distance('山东话','山东方言')==2
    assert distance('','abc')==3
    assert distance('一样','一样')==0

@pytest.mark.parametrize('name,kind',[('../escape','file'),('/absolute','file'),('model/../../escape','file'),('other/file','file'),('model/link','symlink'),('model/hardlink','hardlink')])
def test_reject_unsafe_archives(tmp_path,name,kind):
    archive=tmp_path/'bad.tar.bz2'
    with tarfile.open(archive,'w:bz2') as tar:
        info=tarfile.TarInfo(name)
        if kind=='symlink':info.type=tarfile.SYMTYPE;info.linkname='/etc/passwd'
        elif kind=='hardlink':info.type=tarfile.LNKTYPE;info.linkname='/etc/passwd'
        else:info.size=1
        tar.addfile(info,io.BytesIO(b'x') if kind=='file' else None)
    dest=tmp_path/'extract';dest.mkdir()
    with pytest.raises(ValueError):safe_extract(archive,dest,'model')

def test_extract_and_lock(tmp_path):
    archive=tmp_path/'ok.tar.bz2'
    with tarfile.open(archive,'w:bz2') as tar:
        info=tarfile.TarInfo('./model/weights.bin');info.size=3;tar.addfile(info,io.BytesIO(b'abc'))
    dest=tmp_path/'extract';dest.mkdir();safe_extract(archive,dest,'model')
    manifest=file_manifest(dest,'model');verify_manifest(dest,manifest)
    (dest/'model/weights.bin').write_bytes(b'bad')
    with pytest.raises(ValueError):verify_manifest(dest,manifest)

def test_config_file_permissions(tmp_path):
    path=tmp_path/'.env';write_env(path,{'HELLO':'山东话','A':'123'})
    assert load_env(path)=={'HELLO':'山东话','A':'123'} and path.stat().st_mode & 0o777==0o600
    with pytest.raises(ValueError):write_env(path,{'X':'$(evil)'})

class TailVad:
    def __init__(self):self.audio=[];self.queue=[];self.active=False
    def accept_waveform(self,x):self.audio.append(x.copy());self.active=True
    def is_speech_detected(self):return self.active
    def empty(self):return not self.queue
    @property
    def front(self):return self.queue[0]
    def pop(self):self.queue.pop(0)
    def flush(self):
        if self.audio:self.queue.append(SimpleNamespace(start=0,samples=np.concatenate(self.audio)));self.audio=[]
        self.active=False

def test_vad_flush_tail_and_bounded_preview():
    vad=TailVad();segmenter=Segmenter(vad,4)
    assert not segmenter.feed(np.ones(16001,np.float32))
    preview=segmenter.preview();assert preview[0]==0
    final=segmenter.flush();assert final[0].id==0
    assert len(final[0].samples)==16384 and np.all(final[0].samples[:16001]==1)
    assert segmenter.flush()==[]
    segmenter=Segmenter(TailVad(),4);segmenter.feed(np.ones(16000*20,np.float32))
    assert len(segmenter.buffer)<=16000*7 and len(segmenter.pending)<512

async def test_worker_priority_and_preview_drop():
    worker=InferenceWorker();await worker.start()
    started=threading.Event();release=threading.Event();order=[]
    def hold():started.set();release.wait(2);order.append('running')
    current=asyncio.create_task(worker.submit(hold,1))
    await asyncio.to_thread(started.wait,1)
    with pytest.raises(BusyError):await worker.submit(lambda:None,3)
    tts=asyncio.create_task(worker.submit(lambda:order.append('tts'),2))
    final=asyncio.create_task(worker.submit(lambda:order.append('final'),0))
    await asyncio.sleep(.01);release.set();await asyncio.gather(current,tts,final)
    assert order==['running','final','tts'];await worker.close()

async def test_worker_stale_and_errors():
    worker=InferenceWorker();await worker.start()
    assert await worker.submit(lambda:'should-not-run',3,lambda:False) is None
    def fail():raise ValueError('expected-test-error')
    with pytest.raises(ValueError):await worker.submit(fail,0)
    assert await worker.submit(lambda:42,0)==42;await worker.close()
