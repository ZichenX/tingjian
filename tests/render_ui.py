#!/usr/bin/env python3
"""Offline Chromium DOM/layout tests. Uses in-memory fetch fixtures, NO network or microphone.
This is separate from browser_smoke.py's end-to-end harness, which requires a normal test browser.
"""
import argparse
import base64
import json
import re
import shutil
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
ROOT=Path(__file__).resolve().parents[1]

FETCH_FIXTURE=r'''
window.fetch=async function(path, options={}) {
  let headers={'Content-Type':'application/json'};
  const result=(value,status=200)=>Promise.resolve(new Response(JSON.stringify(value),{status,headers}));
  if(path==='/api/session')return result({authenticated:true,auth_required:false,csrf:'offline-dom-test',tts:true,partial:true,engine:'dual',max_upload_mb:50,max_upload_seconds:600,max_session_seconds:1800});
  throw new Error('Offline DOM test does not implement this API: '+path);
};
'''

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir',type=Path,default=ROOT/'reports/ui')
    p.add_argument('--chromium',default=shutil.which('chromium'))
    args=p.parse_args();args.output_dir.mkdir(parents=True,exist_ok=True)
    html=(ROOT/'web/index.html').read_text()
    html=re.sub(r'<script\b[^>]*>.*?</script>', '', html, flags=re.S)
    html=re.sub(r'<link\b[^>]*>', '', html)
    svg=(ROOT/'web/icon.svg').read_text()
    html=re.sub(r'<img\b[^>]*src="/assets/icon.svg"[^>]*>',svg,html)
    html=html.replace('</head>','<style>'+(ROOT/'web/style.css').read_text()+'\n.brand>svg{width:44px;height:44px}.dialog-logo{width:56px;height:56px}</style></head>')
    checks=[];errors=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=args.chromium,headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1280,'height':1100},locale='zh-CN')
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.set_content(html)
        page.add_script_tag(content=FETCH_FIXTURE)
        page.add_script_tag(content=(ROOT/'web/text.js').read_text())
        page.add_script_tag(content=(ROOT/'web/app.js').read_text())
        expect(page.locator('#loginDialog')).to_have_count(0)
        expect(page.locator('#record')).to_be_enabled()
        checks.append('direct entry without an access-code dialog with in-memory API fixtures')
        page.evaluate("addFinal(1,{id:1,text:'第二句'});addFinal(1,{id:0,text:'第一句'});")
        assert page.locator('#finals p').all_text_contents()==['第一句','第二句']
        page.evaluate("showPartial(1,{id:2,text:'未确认的第三句'});uncertainPartial();")
        expect(page.locator('.partial-label')).to_contain_text('未确认')
        assert page.evaluate('TingjianText.toPlainText(records)')=='第一句\n第二句'
        page.evaluate("addFinal(1,{id:2,text:'第三句定稿'});showPartial(1,{id:2,text:'迟到预览'});")
        expect(page.locator('#partialBox')).not_to_be_visible()
        checks.append('final ordering, uncertain partial excluded from export, stale preview cannot overwrite final')
        page.evaluate("addFinal(100,{id:0,text:'<img src=x onerror=alert(1)>测试'});")
        assert page.locator('#finals img').count()==0
        checks.append('HTML-like recognition text renders safely as literal text')
        page.locator('#more summary').click();page.locator('#clear').click()
        page.locator('#clearCancel').click();expect(page.locator('#finals p')).to_have_count(4)
        page.locator('#clear').click();page.locator('#clearConfirm').click();expect(page.locator('#finals p')).to_have_count(0)
        checks.append('explicit clear confirmation and cancellation')
        page.locator('#read').click();expect(page.locator('#readDialog')).to_be_visible()
        page.locator('#speakText').fill('帮我把这句话读出来。');assert page.locator('#speakText').input_value()=='帮我把这句话读出来。'
        page.locator('#readClose').click();expect(page.locator('#readDialog')).not_to_be_visible()
        page.evaluate("setState('listening')");expect(page.locator('#read')).to_be_disabled()
        page.evaluate("setState('idle')");expect(page.locator('#read')).to_be_enabled()
        checks.append('editable read-aloud dialog; playback entry disabled while listening')
        page.evaluate("""records.clear();document.getElementById('finals').replaceChildren();notice();
            document.getElementById('partialBox').hidden=true;
            addFinal(1,{id:0,text:'今天咱们去公园走走，回来的时候捎点菜。'});
            addFinal(1,{id:1,text:'您慢慢说，俺看屏幕就能明白。'});
            setState('idle');document.getElementById('counter').textContent='界面演示 · 非识别实测';
            document.getElementById('more').open=false;
            document.getElementById('captions').scrollTop=0;""")
        page.screenshot(path=str(args.output_dir/'desktop.png'),full_page=True)
        metrics=[]
        for width in [320,360,390,768]:
            page.set_viewport_size({'width':width,'height':844});page.locator('[data-size="largest"]').click()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),width
            assert page.locator('#record').bounding_box()['height']>=48
            font=float(page.locator('#finals p').first.evaluate('el=>parseFloat(getComputedStyle(el).fontSize)'))
            assert font>=50
            metrics.append({'width':width,'largest_caption_px':font,'horizontal_overflow':False})
        checks.append('320/360/390/768 px layouts: largest font and no horizontal page overflow')
        page.set_viewport_size({'width':390,'height':844});page.locator('[data-size="large"]').click()
        page.evaluate("document.getElementById('captions').scrollTop=0")
        page.wait_for_timeout(250)
        page.screenshot(path=str(args.output_dir/'mobile.png'),full_page=True)
        page.locator('#read').click();page.screenshot(path=str(args.output_dir/'mandarin.png'),full_page=True)
        assert not errors, errors
        checks.append('no uncaught JavaScript exceptions during these DOM interactions')
        browser.close()
    report={'passed':True,'kind':'offline_dom_and_layout_only','checks':checks,'viewport_metrics':metrics,'javascript_errors':errors,
            'not_tested':['real network in browser','AudioWorklet microphone capture','actual model recognition','actual TTS playback','public HTTPS','Safari or physical phones']}
    (args.output_dir/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
