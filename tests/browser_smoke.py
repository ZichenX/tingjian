#!/usr/bin/env python3
"""Real Chromium/UI/HTTP/WS/AudioWorklet + explicit FAKE ASR/TTS.
Only binds 127.0.0.1. Never deploy this test harness or present its text as recognition accuracy.
"""
from __future__ import annotations
import argparse
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import uvicorn
from playwright.sync_api import sync_playwright, expect
from app.audio import wav_bytes
from app.config import Settings
from app.main import create_app
from app.security import hash_code
from tests.fakes import FakeEngine


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir',type=Path,default=Path('reports/browser'))
    p.add_argument('--chromium',default=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'))
    args=p.parse_args();args.output_dir.mkdir(parents=True,exist_ok=True)
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    origin=f'http://127.0.0.1:{port}';code='test-only-code-123'
    settings=Settings(origin=origin,secret='browser-test-secret-'*4,code_hash=hash_code(code),session_seconds=60,max_upload_seconds=10)
    app=create_app(settings,FakeEngine)
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='warning',access_log=False))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    for _ in range(100):
        if server.started:break
        time.sleep(.05)
    if not server.started:raise RuntimeError('Local test server did not start')
    checks=[];errors=[]
    try:
        with tempfile.TemporaryDirectory() as temporary, sync_playwright() as playwright:
            wav=Path(temporary)/'browser-test-tone.wav'
            wav.write_bytes(wav_bytes(.15*np.sin(np.arange(16000*3)*2*np.pi*440/16000),16000))
            browser=playwright.chromium.launch(executable_path=args.chromium,headless=True,args=[
                '--no-sandbox','--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream',
                f'--use-file-for-fake-audio-capture={wav}'])
            context=browser.new_context(viewport={'width':1280,'height':1100},permissions=['microphone'],locale='zh-CN')
            page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(origin);expect(page.locator('#loginDialog')).to_be_visible()
            page.locator('#code').fill(code);page.locator('#loginSubmit').click()
            expect(page.locator('#loginDialog')).not_to_be_visible();expect(page.locator('#record')).to_be_enabled()
            checks.append('access-code login through actual browser/HTTP')
            page.locator('[data-size="largest"]').click()
            assert page.locator('body').get_attribute('data-font')=='largest'
            page.reload();expect(page.locator('#record')).to_be_enabled()
            assert page.locator('body').get_attribute('data-font')=='largest'
            checks.append('HttpOnly cookie session + font preference survive reload')
            page.locator('[data-size="large"]').click()
            page.locator('#more summary').click();page.locator('#file').set_input_files(str(wav))
            expect(page.locator('#statusText')).to_have_text('录音识别完成',timeout=15000)
            expect(page.locator('#finals p')).to_have_count(2)
            checks.append('upload: real WAV decoding + streamed NDJSON + fake ASR captions')
            with page.expect_download() as download:
                page.locator('#save').click()
            saved=Path(temporary)/'transcript.txt';download.value.save_as(saved)
            assert '界面测试文字' in saved.read_text(encoding='utf-8-sig')
            checks.append('save finalized text as UTF-8 TXT')
            page.locator('#read').click();expect(page.locator('#readDialog')).to_be_visible()
            page.locator('#speakText').fill('这是一段测试。');page.locator('#speak').click()
            expect(page.locator('#speechStatus')).to_have_text('朗读完成。',timeout=10000)
            page.locator('#readClose').click();checks.append('editable TTS dialog + actual WAV browser playback (fake synthesizer)')
            page.locator('#clear').click();expect(page.locator('#confirmDialog')).to_be_visible()
            page.locator('#clearCancel').click();expect(page.locator('#finals p')).to_have_count(2)
            page.locator('#clear').click();page.locator('#clearConfirm').click();expect(page.locator('#finals p')).to_have_count(0)
            checks.append('clear confirmation prevents accidental loss')
            # This traverses getUserMedia -> real AudioWorklet PCM -> real WS -> resampling -> fake ASR.
            page.locator('#record').click();expect(page.locator('#statusText')).to_have_text('正在听，请说话',timeout=15000)
            page.wait_for_timeout(1300);page.locator('#record').click()
            expect(page.locator('#recordLabel')).to_have_text('开始听写',timeout=15000)
            assert page.locator('#finals p').count()>=1
            expect(page.locator('#read')).to_be_enabled()
            checks.append('actual AudioWorklet + WS PCM + stop-flush yields final tail segment')
            # Rendering alone must not execute markup originating in recognition text.
            page.evaluate("addFinal(100,{id:0,text:'<img src=x onerror=alert(1)>测试'});")
            assert page.locator('#finals img').count()==0
            checks.append('recognition text uses textContent, not executable HTML')
            # Simulate transport interruption while listening. Existing confirmed text survives.
            before=page.locator('#finals p').count();page.locator('#record').click()
            expect(page.locator('#statusText')).to_have_text('正在听，请说话',timeout=15000)
            page.evaluate('mic.socket.close()')
            expect(page.locator('#notice')).to_contain_text('中断',timeout=10000)
            assert page.locator('#finals p').count()>=before
            expect(page.locator('#record')).to_be_enabled()
            checks.append('disconnect stops recording without deleting finalized text')
            # Visual preview explicitly uses demonstration text, never attributed to a model.
            page.evaluate("""records.clear();document.getElementById('finals').replaceChildren();
                document.getElementById('partialBox').hidden=true;notice();
                addFinal(1,{id:0,text:'今天咱们去公园走走，回来的时候捎点菜。'});
                addFinal(1,{id:1,text:'您慢慢说，俺看屏幕就能明白。'});
                setState('idle');document.getElementById('counter').textContent='界面演示 · 非识别实测';
                document.getElementById('more').open=false;
                document.getElementById('captions').scrollTop=0;""")
            page.screenshot(path=str(args.output_dir/'desktop.png'),full_page=True)
            for width in [320,360,390,768]:
                page.set_viewport_size({'width':width,'height':844})
                page.locator('[data-size="largest"]').click()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),f'horizontal overflow at {width}'
                assert page.locator('#record').bounding_box()['height']>=48
            checks.append('320/360/390/768 px layouts, largest font, no horizontal page overflow')
            page.set_viewport_size({'width':390,'height':844});page.locator('[data-size="large"]').click()
            page.evaluate("document.getElementById('captions').scrollTop=0")
            page.screenshot(path=str(args.output_dir/'mobile.png'),full_page=True)
            page.locator('#read').click();page.screenshot(path=str(args.output_dir/'mandarin.png'),full_page=True)
            page.locator('#readClose').click()
            assert not errors,errors
            checks.append('no uncaught JavaScript exceptions')
            browser.close()
    finally:
        server.should_exit=True;thread.join(timeout=10)
    report={'passed':True,'checks':checks,'javascript_errors':errors,
            'boundaries':'Real Chromium + HTTP/WS/audio transport/ffmpeg/soxr. ASR, VAD and TTS are explicit test doubles. Not proof of model accuracy, real phone microphone, Docker or public TLS.'}
    (args.output_dir/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
